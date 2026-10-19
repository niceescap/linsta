"""Technical authorization document contract, not a production licence.

There is deliberately no production-approved document. No environment variable,
request flag or caller-created document can approve one. A future reviewed code
change must register the legally validated version; retain older versions forever.
SQLite connections must use db.get_db() to install the integrity functions.
"""
from dataclasses import dataclass
import hashlib
from types import MappingProxyType


class RightsError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AuthorizationDocument:
    version: str
    music_clause: str
    visual_clause: str
    conditions: str

    @property
    def text(self):
        return (f'Document {self.version}\n\n1. Musique\n{self.music_clause}'
                f'\n\n2. Cover\n{self.visual_clause}\n\nConditions communes\n{self.conditions}\n')

    @property
    def sha256(self):
        return hashlib.sha256(self.text.encode('utf-8')).hexdigest()


DRAFT_DOCUMENT = AuthorizationDocument(
    version='draft-media-v1',
    music_clause='BROUILLON TECHNIQUE : clause musicale à rédiger et valider juridiquement.',
    visual_clause='BROUILLON TECHNIQUE : clause visuelle à rédiger et valider juridiquement.',
    conditions=('NON ACTIVABLE EN PRODUCTION. Usages radio, lecteur, catalogue, site, application '
                'et promotion : périmètre à valider. Durée, territoire, rémunération, '
                'transformations et retrait : conditions non définies.'))
_DOCUMENTS = MappingProxyType({DRAFT_DOCUMENT.version: DRAFT_DOCUMENT})
_PRODUCTION_VERSIONS = frozenset()


def document_matches(version, text, digest):
    """SQLite-safe exact registry match, including the UTF-8 text digest."""
    if not all(isinstance(value, str) for value in (version, text, digest)):
        return 0
    document = _DOCUMENTS.get(version)
    return int(document is not None and text == document.text and digest == document.sha256)


def document_is_production_ready(version, text, digest):
    return int(bool(document_matches(version, text, digest)) and version in _PRODUCTION_VERSIONS)


def validate_acceptances(music_accepted, visual_accepted):
    if music_accepted is not True or visual_accepted is not True:
        raise RightsError('acceptance_required', 'Les deux acceptations explicites sont obligatoires.')


def identity_is_valid(value):
    return int(isinstance(value, str) and 1 <= len(value.strip()) <= 256
               and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def validate_authorization(*, music_accepted, visual_accepted, declared_name,
                           declared_artist, declarant_capacity, document=DRAFT_DOCUMENT,
                           for_production=True):
    """Validate data only; no SQLite, uploads, account authentication or legal advice.

    for_production=False is explicit draft inspection/testing, never an activation.
    The production default fails closed until a reviewed version is registered.
    """
    validate_acceptances(music_accepted, visual_accepted)
    if type(for_production) is not bool:
        raise RightsError('invalid_option', 'Mode de validation invalide.')
    for value in (declared_name, declared_artist):
        if not identity_is_valid(value):
            raise RightsError('invalid_identity', 'Une identité déclarée valide est requise.')
    if declarant_capacity not in ('rights_holder', 'authorized_representative'):
        raise RightsError('invalid_capacity', 'Qualité du déclarant invalide.')
    if (not isinstance(document, AuthorizationDocument)
            or not document_matches(document.version, document.text, document.sha256)):
        raise RightsError('unknown_document', 'Document inconnu ou modifié.')
    if for_production and not document_is_production_ready(document.version, document.text, document.sha256):
        raise RightsError('document_not_validated', 'Aucun document juridiquement validé pour la production.')
    return {'declared_name': declared_name.strip(), 'declared_artist': declared_artist.strip(),
            'declarant_capacity': declarant_capacity, 'music_accepted': 1, 'visual_accepted': 1,
            'document_version': document.version, 'document_text': document.text,
            'document_sha256': document.sha256}
