"""Owned MP3+cover deposit and final review, independent of Flask and providers."""
from contextlib import ExitStack
import os
from pathlib import Path
import sqlite3

from audio_storage import (MediaLimits, StorageError, StorageSession, checked_open,
                           identity, prepare_audio)
from cover_storage import prepare_cover
import rights
from metadata import normalize_source_url, MetadataURLValidationError
from submissions import SubmissionError, _id, _editorial_text
from db import finalize_audio_review


def fail(code,message):
    raise SubmissionError(code,message)


class SubmissionAudioService:
    def __init__(self,conn,storage_root,meta_root,limits=None):
        self.conn=conn
        self.storage_root=Path(storage_root)
        self.meta_root=Path(meta_root)
        self.limits=limits or MediaLimits()

    def ready(self):
        if self.conn.in_transaction:
            fail('active_transaction','Une connexion sans transaction active est requise.')
        if self.conn.execute('PRAGMA foreign_keys').fetchone()[0]!=1:
            fail('foreign_keys_disabled','Les clés étrangères doivent être activées.')

    def actor(self,actor_id,*,admin=False):
        _id(actor_id)
        row=self.conn.execute('SELECT role,is_verified FROM users WHERE id=?',(actor_id,)).fetchone()
        if row is None or not row['is_verified'] or row['role'] not in (('admin',) if admin else ('contributor','admin')):
            fail('unauthorized','Un compte administrateur vérifié est requis.' if admin else 'Un compte vérifié est requis.')

    def row(self,actor_id,sid,*,admin=False):
        self.actor(actor_id,admin=admin)
        _id(sid)
        row=self.conn.execute('SELECT * FROM submissions WHERE id=?'+('' if admin else ' AND contributor_id=?'),
                              (sid,) if admin else (sid,actor_id)).fetchone()
        if row is None: fail('not_found','Proposition introuvable.')
        return dict(row)

    def get(self,actor_id,sid,*,admin=False,awaiting=False):
        self.ready()
        row=self.row(actor_id,sid,admin=admin)
        if awaiting and (row['status']!='approved_awaiting_audio' or row['track_id'] is not None):
            fail('invalid_state','Le dépôt nécessite une proposition acceptée sans audio déjà déposé.')
        return row

    def _evidence(self,sid):
        row=self.conn.execute('''SELECT b.*,a.declared_name,a.declared_artist,a.declarant_capacity,
            a.music_accepted,a.visual_accepted,a.accepted_at,a.document_version,a.document_text,a.document_sha256
            FROM submission_media_bundles b JOIN submission_authorizations a ON a.bundle_id=b.id
            WHERE b.submission_id=?''',(sid,)).fetchone()
        if row is None: fail('missing_evidence','Les preuves MP3, cover et autorisations sont incomplètes.')
        return dict(row)

    def deposit(self,actor_id,sid,*,audio,cover,declared_name,declared_artist,declarant_capacity,
                music_accepted,visual_accepted,document_version,document_sha256):
        row=self.get(actor_id,sid,awaiting=True)
        try:
            source=normalize_source_url(row['source_url'])
            if (source.platform,source.source_id)!=(row['platform'],row['source_id']):
                fail('invalid_source','La source de la proposition est incohérente.')
        except MetadataURLValidationError:
            fail('invalid_source','Une URL YouTube ou Spotify valide est obligatoire.')
        document=rights.DRAFT_DOCUMENT
        if document_version!=document.version or document_sha256!=document.sha256:
            fail('invalid_document','Le document a changé. Relisez-le avant de confirmer.')
        authorization=rights.validate_authorization(music_accepted=music_accepted,visual_accepted=visual_accepted,
            declared_name=declared_name,declared_artist=declared_artist,declarant_capacity=declarant_capacity,
            document=document,for_production=False)  # Evidence only; final approval remains independently gated.
        if audio is None or cover is None or not audio.filename or not cover.filename:
            fail('invalid_file','Le MP3 et la cover doivent être fournis ensemble.')
        session=None
        committed=False
        try:
            session=StorageSession(self.storage_root,self.meta_root)
            prepared_audio=prepare_audio(session,audio.stream,audio.filename,self.limits)
            original,display=prepare_cover(session,cover.stream,cover.filename,self.limits)
            audio_name=f'digger-{session.token}.mp3'
            original_name=f'digger-{session.token}-original'+('.png' if original.mime=='image/png' else '.jpg')
            display_name=f'digger-{session.token}-display'+('.png' if display.mime=='image/png' else '.jpg')
            with self.conn:
                self.conn.execute('BEGIN IMMEDIATE')
                row=self.row(actor_id,sid)
                if row['status']!='approved_awaiting_audio' or row['track_id'] is not None:
                    fail('invalid_state','Un dépôt a déjà été reçu ou cette proposition n’est plus ouverte.')
                if not _editorial_text(row['title']) or not _editorial_text(row['artist']) or not row['genre_id']:
                    fail('incomplete_editorial','Titre, artiste et genre sont obligatoires.')
                tid=self.conn.execute('''INSERT INTO tracks(uploader_id,file_path,file_size,mime_type,
                    original_filename,title,artist,genre_id,duration_seconds,source_url,meta_path,status)
                    VALUES (?,?,?,'audio/mpeg',?,?,?,?,?,?,?,'pending')''',
                    (actor_id,'tracks/'+audio_name,prepared_audio.size,prepared_audio.original_filename,
                     row['title'],row['artist'],row['genre_id'],prepared_audio.duration,row['canonical_url'],display_name)).lastrowid
                bundle=dict(submission_id=sid,contributor_id=actor_id,track_id=tid,
                    audio_path='tracks/'+audio_name,audio_sha256=prepared_audio.sha256,
                    audio_size=prepared_audio.size,audio_duration_seconds=prepared_audio.duration,
                    cover_original_path=original_name,cover_original_sha256=original.sha256,
                    cover_original_size=original.size,cover_original_mime=original.mime,
                    cover_original_width=original.width,cover_original_height=original.height,
                    cover_normalized_path=display_name,cover_normalized_sha256=display.sha256,
                    cover_normalized_size=display.size,cover_normalized_mime=display.mime,
                    cover_normalized_width=display.width,cover_normalized_height=display.height)
                bid=self.conn.execute(f'INSERT INTO submission_media_bundles ({",".join(bundle)}) VALUES ({",".join("?" for _ in bundle)})',tuple(bundle.values())).lastrowid
                proof=dict(bundle_id=bid,contributor_id=actor_id,**authorization)
                self.conn.execute(f'INSERT INTO submission_authorizations ({",".join(proof)}) VALUES ({",".join("?" for _ in proof)})',tuple(proof.values()))
                session.publish(prepared_audio,session.tracks,audio_name)
                session.publish(original,session.meta,original_name)
                session.publish(display,session.meta,display_name)
                changed=self.conn.execute("UPDATE submissions SET track_id=?,status='pending_audio_review',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='approved_awaiting_audio' AND track_id IS NULL",(tid,sid))
                if changed.rowcount!=1: fail('invalid_state','Un dépôt concurrent a déjà été reçu.')
            committed=True
        except sqlite3.IntegrityError as error:
            raise SubmissionError('invalid_state','Le dépôt n’a pas pu être rattaché. Vérifiez son statut avant de réessayer.') from error
        except sqlite3.Error as error:
            raise SubmissionError('storage_error','Le dépôt a rencontré une erreur de base. Consultez son statut avant de réessayer.') from error
        except OSError as error:
            raise StorageError('storage_error','Le stockage est indisponible. Aucun dépôt complet n’a été validé.') from error
        finally:
            if session is not None:
                # A commit error can be ambiguous. Preserve published files whenever
                # a reference exists, or when checking references is not possible.
                keep=committed
                if not keep and session.published:
                    try:
                        if self.conn.in_transaction: keep=True
                        else:
                            keep=bool(self.conn.execute('SELECT 1 FROM submission_media_bundles WHERE audio_path=?',
                                ('tracks/'+f'digger-{session.token}.mp3',)).fetchone())
                    except sqlite3.Error: keep=True
                session.cleanup(keep_published=keep)
        return self.get(actor_id,sid)

    def _verified_files(self,evidence,stack):
        if not rights.document_matches(evidence['document_version'],evidence['document_text'],evidence['document_sha256']):
            fail('invalid_document','Le document d’autorisation ne correspond pas à sa version.')
        if evidence['music_accepted']!=1 or evidence['visual_accepted']!=1:
            fail('missing_evidence','Les deux autorisations sont obligatoires.')
        files={}
        specs=[('audio',self.storage_root/'tracks',Path(evidence['audio_path']).name,evidence['audio_sha256'],evidence['audio_size']),
               ('original',self.meta_root,evidence['cover_original_path'],evidence['cover_original_sha256'],evidence['cover_original_size'])]
        if evidence['cover_normalized_path']:
            specs.append(('cover',self.meta_root,evidence['cover_normalized_path'],evidence['cover_normalized_sha256'],evidence['cover_normalized_size']))
        for kind,root,name,digest,size in specs:
            stream,snapshot=checked_open(root,name,sha256=digest,size=size)
            stack.enter_context(stream)
            files[kind]=(stream,snapshot,root/name)
        if 'cover' not in files: files['cover']=files['original']
        return files

    def review(self,admin_id,sid,*,approve,reason=None,listened=False,cover_checked=False):
        row=self.get(admin_id,sid,admin=True)
        if type(approve) is not bool: fail('invalid_option','Décision invalide.')
        if row['status']!='pending_audio_review': fail('invalid_state','Cette proposition n’attend pas de revue finale.')
        reason=_editorial_text(reason)
        if not approve and not reason: fail('missing_reason','Un motif de refus est obligatoire.')
        with ExitStack() as stack:
            try:
                proof=self._evidence(sid)
            except SubmissionError as error:
                if approve or error.code!='missing_evidence': raise
                proof=None  # Older pending audio may be refused, never approved without evidence.
            files={}
            if approve:
                if listened is not True or cover_checked is not True:
                    fail('review_required','Confirmez l’écoute du MP3 et la vérification de la cover.')
                if not rights.document_is_production_ready(proof['document_version'],proof['document_text'],proof['document_sha256']):
                    fail('document_not_validated','Approbation bloquée : le document n’est pas validé juridiquement.')
                files=self._verified_files(proof,stack)  # hashing outside SQLite transaction
            def precondition(conn, linked):
                current=self.row(admin_id,sid,admin=True)
                try:
                    current_proof=self._evidence(sid)
                except SubmissionError as error:
                    if proof is not None or error.code!='missing_evidence': raise
                    current_proof=None
                if (current['status']!='pending_audio_review' or current['track_id']!=row['track_id']
                        or current_proof!=proof or linked['track_id']!=row['track_id']):
                    fail('invalid_state','La proposition a changé pendant la revue.')
                track=conn.execute('SELECT * FROM tracks WHERE id=?',(row['track_id'],)).fetchone()
                if (track is None or track['status']!='pending' or track['uploader_id']!=current['contributor_id']
                        or (proof is not None and (track['file_path']!=proof['audio_path']
                        or track['file_size']!=proof['audio_size']
                        or track['meta_path']!=(proof['cover_normalized_path'] or proof['cover_original_path'])))):
                    fail('media_integrity','Le track ne correspond pas aux fichiers autorisés.')
                for stream,snapshot,path in files.values():
                    if identity(os.fstat(stream.fileno()))!=snapshot or identity(path.stat(follow_symlinks=False))!=snapshot:
                        fail('media_integrity','Un média a changé pendant la revue.')
            try:
                finalize_audio_review(self.conn,sid,admin_id,approve=approve,reason=reason,precondition=precondition)
            except ValueError as error:
                if isinstance(error,SubmissionError): raise
                fail('invalid_state','La proposition a déjà été traitée ou n’est plus disponible.')
            except OSError as error:
                raise StorageError('media_missing','Un média est devenu inaccessible.') from error
            except sqlite3.Error as error:
                raise SubmissionError('invalid_state','La revue n’a pas pu être validée. Consultez le statut de la proposition.') from error
        return sid

    def open_private(self,actor_id,sid,kind,*,admin=False):
        row=self.get(actor_id,sid,admin=admin)
        if kind not in ('audio','cover'): fail('not_found','Média introuvable.')
        if row['track_id'] is None: fail('not_found','Aucun média déposé.')
        proof=self._evidence(sid)
        # Verify only the selected media for playback; approval verifies all three.
        if kind=='audio': root,name,digest,size=self.storage_root/'tracks',Path(proof['audio_path']).name,proof['audio_sha256'],proof['audio_size']
        else:
            prefix='cover_normalized' if proof['cover_normalized_path'] else 'cover_original'
            root,name,digest,size=self.meta_root,proof[prefix+'_path'],proof[prefix+'_sha256'],proof[prefix+'_size']
        stream,_=checked_open(root,name,sha256=digest,size=size)
        mime='audio/mpeg' if kind=='audio' else proof[prefix+'_mime']
        return stream,mime
