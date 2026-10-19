"""Bounded private local storage. No HTTP, database, transcoding or global config.

Only files created by a StorageSession are eligible for its cleanup. Publication
uses exclusive hard links on the same filesystem, never os.replace(). Crash
orphans are inventoried, not automatically purged while an upload may be active.
"""
from dataclasses import dataclass
import hashlib
import logging
import math
import os
from pathlib import Path
import stat
import tempfile
import uuid

from mutagen.mp3 import MP3
from werkzeug.utils import secure_filename


class StorageError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class MediaLimits:
    audio_bytes: int = 50 * 1024 * 1024
    duration_seconds: int = 1800
    cover_bytes: int = 8 * 1024 * 1024
    cover_pixels: int = 20_000_000
    cover_dimension: int = 8192

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in self.__dict__.values()):
            raise ValueError('Media limits must be positive integers')

    @classmethod
    def from_environment(cls, environ):
        return cls(**{name:int(environ.get('DIGGER_'+name.upper(), default))
                      for name,default in cls().__dict__.items()})

    @property
    def request_bytes(self):
        return self.audio_bytes + self.cover_bytes + 65536


@dataclass(frozen=True)
class PreparedFile:
    path: Path
    sha256: str
    size: int
    identity: tuple
    original_filename: str
    duration: float = 0
    mime: str = ''
    width: int = 0
    height: int = 0


def identity(st):
    return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)


def safe_directory(path, *, create=False):
    path=Path(path).absolute()
    # Configured roots are trusted paths, but never follow a substituted directory.
    for component in reversed((path,*path.parents)):
        if component.exists() or component.is_symlink():
            if component.is_symlink() or not component.is_dir():
                raise StorageError('storage_error','Répertoire de stockage indisponible.')
        elif create:
            component.mkdir(mode=0o700,exist_ok=True)
            if component.is_symlink() or not component.is_dir():
                raise StorageError('storage_error','Répertoire de stockage indisponible.')
        else:
            raise StorageError('media_missing','Fichier média indisponible.')
    return path


def checked_open(root, name, *, sha256=None, size=None):
    """Open a verified regular file, returning the actual FD, not a racy pathname."""
    if not isinstance(name,str) or not name or Path(name).name != name or '\\' in name or '\0' in name:
        raise StorageError('media_integrity','Identité du fichier invalide.')
    root=safe_directory(root)
    try:
        fd=os.open(root/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        stream=os.fdopen(fd,'rb')
        try:
            before=os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise StorageError('media_integrity','Le média doit être un fichier régulier.')
            if size is not None and before.st_size != size:
                raise StorageError('media_integrity','Le fichier ne correspond plus à son autorisation.')
            if sha256 is not None:
                digest=hashlib.sha256()
                for chunk in iter(lambda:stream.read(65536),b''): digest.update(chunk)
                if digest.hexdigest()!=sha256:
                    raise StorageError('media_integrity','Empreinte du média incorrecte.')
            if identity(before)!=identity(os.fstat(fd)) or identity(before)!=identity(os.stat(root/name,follow_symlinks=False)):
                raise StorageError('media_integrity','Le média a changé pendant sa vérification.')
            stream.seek(0)
            return stream,identity(before)
        except BaseException:
            stream.close()
            raise
    except OSError as error:
        raise StorageError('media_missing','Fichier média absent ou inaccessible.') from error


class StorageSession:
    def __init__(self, storage_root, meta_root):
        self.storage_root=Path(storage_root).absolute()
        self.tracks=safe_directory(self.storage_root/'tracks',create=True)
        self.meta=safe_directory(meta_root,create=True)
        self.staging=safe_directory(self.tracks/'.incoming',create=True)
        self.owned={}
        self.published=set()
        self.token=uuid.uuid4().hex

    def new_file(self):
        fd,path=tempfile.mkstemp(prefix='digger-',suffix='.part',dir=self.staging)
        path=Path(path)
        self.owned[path]=(os.fstat(fd).st_dev,os.fstat(fd).st_ino)
        return os.fdopen(fd,'wb'),path

    def receive(self, stream, filename, limit):
        if not hasattr(stream,'read'):
            raise StorageError('invalid_file','Fichier requis.')
        output,path=self.new_file()
        count=0
        with output:
            while True:
                chunk=stream.read(min(65536,limit-count+1))
                if not chunk: break
                count+=len(chunk)
                if count>limit:
                    raise StorageError('file_too_large','Le fichier dépasse la taille autorisée.')
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if not count:
            raise StorageError('invalid_file','Le fichier est vide.')
        return self.describe(path,filename)

    def describe(self,path,filename,**details):
        stream,snapshot=checked_open(path.parent,path.name)
        with stream:
            digest=hashlib.sha256()
            for chunk in iter(lambda:stream.read(65536),b''): digest.update(chunk)
            if snapshot!=identity(os.fstat(stream.fileno())):
                raise StorageError('media_integrity','Le fichier a changé pendant sa préparation.')
        return PreparedFile(path,digest.hexdigest(),snapshot[2],snapshot,
                            secure_filename(filename or '')[:240] or 'upload',**details)

    def publish(self,prepared,root,name):
        root=safe_directory(root)
        if self.owned.get(prepared.path)!=prepared.identity[:2] or prepared.path.parent!=self.staging:
            raise StorageError('media_integrity','Fichier temporaire inconnu.')
        if identity(prepared.path.stat(follow_symlinks=False))!=prepared.identity:
            raise StorageError('media_integrity','Fichier temporaire remplacé ou modifié.')
        if Path(name).name!=name or not name.startswith('digger-'):
            raise StorageError('media_integrity','Destination de publication invalide.')
        os.chmod(prepared.path,0o400,follow_symlinks=False)
        target=root/name
        os.link(prepared.path,target,follow_symlinks=False)  # raises on any existing target
        self.owned[target]=prepared.identity[:2]
        self.published.add(target)
        fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
        return target

    def cleanup(self, *, keep_published=False):
        failures=[]
        for path,inode in tuple(self.owned.items()):
            if keep_published and path in self.published: continue
            try:
                st=path.stat(follow_symlinks=False)
                if (st.st_dev,st.st_ino)!=inode or not stat.S_ISREG(st.st_mode):
                    failures.append(path.name)
                    continue
                path.unlink()
            except FileNotFoundError: pass
            except OSError: failures.append(path.name)
        if failures:
            logging.getLogger(__name__).warning('Media cleanup deferred for %d owned files',len(failures))
        return failures


def prepare_audio(session, stream, filename, limits):
    if not isinstance(filename,str) or not filename.lower().endswith('.mp3'):
        raise StorageError('invalid_audio','Un fichier MP3 est requis.')
    prepared=session.receive(stream,filename,limits.audio_bytes)
    try:
        info=MP3(prepared.path).info
        duration=float(info.length)
        if (info.layer!=3 or info.sketchy or info.channels not in (1,2)
                or info.sample_rate<=0 or info.bitrate<=0 or not math.isfinite(duration) or duration<=0):
            raise ValueError('Invalid MPEG Layer III stream')
        if duration>limits.duration_seconds:
            raise StorageError('audio_too_long','La durée du MP3 dépasse la limite autorisée.')
        return session.describe(prepared.path,filename,duration=duration,mime='audio/mpeg')
    except StorageError: raise
    except Exception as error:
        raise StorageError('invalid_audio','Le contenu du fichier MP3 est invalide.') from error


def orphan_candidates(conn, storage_root, meta_root):
    """Read-only recovery inventory. Candidates may belong to active uploads.

    Never delete automatically: reconcile transactions and active requests first.
    Only this implementation's digger-* names are considered, never legacy media.
    """
    rows=conn.execute('SELECT audio_path,cover_original_path,cover_normalized_path FROM submission_media_bundles').fetchall()
    protected={str(Path(storage_root)/r['audio_path']) for r in rows}
    protected.update(str(Path(meta_root)/r[k]) for r in rows for k in ('cover_original_path','cover_normalized_path') if r[k])
    for r in conn.execute('SELECT file_path,meta_path FROM tracks'):
        protected.add(str(Path(storage_root)/r['file_path']))
        if r['meta_path']: protected.add(str(Path(meta_root)/r['meta_path']))
    candidates=[]
    for root in (Path(storage_root)/'tracks',Path(storage_root)/'tracks'/'.incoming',Path(meta_root)):
        if not root.exists() or root.is_symlink(): continue
        for path in root.glob('digger-*'):
            if path.is_file() and not path.is_symlink() and str(path) not in protected:
                candidates.append(path)
    return candidates
