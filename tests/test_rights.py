"""Draft contracts are inspectable, never production-enabled."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import os
import unittest
from unittest.mock import patch

from rights import (DRAFT_DOCUMENT, RightsError, document_matches,
                    document_is_production_ready, validate_authorization, validate_acceptances)


class RightsTests(unittest.TestCase):
    def arguments(self):
        return dict(music_accepted=True,visual_accepted=True,declared_name=' Declarant ',
                    declared_artist=' Artist ',declarant_capacity='rights_holder')

    def test_two_distinct_clauses_and_unsettled_conditions(self):
        document = DRAFT_DOCUMENT
        self.assertNotEqual(document.music_clause,document.visual_clause)
        for text in ('1. Musique','2. Cover','NON ACTIVABLE EN PRODUCTION','non définies'):
            self.assertIn(text,document.text)
        self.assertEqual(document.sha256,hashlib.sha256(document.text.encode('utf-8')).hexdigest())

    def test_production_is_closed_by_default(self):
        with self.assertRaises(RightsError) as error:
            validate_authorization(**self.arguments())
        self.assertEqual(error.exception.code,'document_not_validated')
        self.assertEqual(document_is_production_ready(DRAFT_DOCUMENT.version,DRAFT_DOCUMENT.text,DRAFT_DOCUMENT.sha256),0)

    def test_explicit_draft_validation_is_not_activation(self):
        result = validate_authorization(**self.arguments(),for_production=False)
        self.assertEqual(result['declared_name'],'Declarant')
        self.assertEqual((result['music_accepted'],result['visual_accepted']),(1,1))
        self.assertEqual(result['document_sha256'],DRAFT_DOCUMENT.sha256)
        with self.assertRaises(RightsError): validate_authorization(**self.arguments())

    def test_acceptances_require_actual_booleans(self):
        for value in (None,False,0,1,'true','on',[],{}):
            for name in ('music_accepted','visual_accepted'):
                args = self.arguments()
                args[name]=value
                with self.subTest(name=name,value=value), self.assertRaises(RightsError):
                    validate_authorization(**args,for_production=False)
        validate_acceptances(True,True)

    def test_identity_and_capacity_validation(self):
        for name in ('declared_name','declared_artist'):
            for value in ('','   ','\t','bad\nvalue','x'*257,123,None):
                args = self.arguments()
                args[name]=value
                with self.subTest(name=name,value=value), self.assertRaises(RightsError):
                    validate_authorization(**args,for_production=False)
        args = self.arguments()
        args['declarant_capacity']='anonymous'
        with self.assertRaises(RightsError): validate_authorization(**args,for_production=False)

    def test_caller_cannot_invent_or_rewrite_document(self):
        for document in (replace(DRAFT_DOCUMENT,version='legally-approved'),
                         replace(DRAFT_DOCUMENT,music_clause='My invented licence'),None):
            with self.assertRaises(RightsError):
                validate_authorization(**self.arguments(),document=document,for_production=False)
        with self.assertRaises(FrozenInstanceError): DRAFT_DOCUMENT.version='approved'

    def test_exact_version_text_digest_match(self):
        d = DRAFT_DOCUMENT
        self.assertEqual(document_matches(d.version,d.text,d.sha256),1)
        for values in ((d.version,d.text,'0'*64),(d.version,d.text+' ',d.sha256),
                       ('unknown',d.text,d.sha256),(d.version,d.text,d.sha256.upper()),
                       (None,d.text,d.sha256),(d.version,None,None)):
            self.assertEqual(document_matches(*values),0)

    def test_environment_does_not_enable_draft(self):
        with patch.dict(os.environ,{'RIGHTS_APPROVED':'1','RIGHTS_DOCUMENT_MODE':'production'}):
            with self.assertRaises(RightsError): validate_authorization(**self.arguments())

    def test_validation_mode_must_be_boolean(self):
        for mode in (0,1,None,'false'):
            with self.assertRaises(RightsError):
                validate_authorization(**self.arguments(),for_production=mode)
