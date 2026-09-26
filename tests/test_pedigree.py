import tempfile
import unittest
from pathlib import Path

from src.domain import (
    Actor,
    ConflictError,
    NotFoundError,
    PermissionDenied,
    ValidationError,
)
from src.repository import SQLiteRepository
from src.rules import RuleEngine
from src.service import DomainService


class PedigreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.tmp.name) / "test.db")
        self.service = DomainService(self.repo, RuleEngine())
        self.admin = Actor("admin", "admin")

    def tearDown(self):
        self.tmp.cleanup()

    def _animal(self, name, sex, **extra):
        data = {"name": name, "sex": sex}
        data.update(extra)
        return self.service.create(self.admin, "animal", data)

    def test_create_with_registered_parents_and_litter(self):
        sire = self._animal("壮壮", "male")
        dam = self._animal("丽丽", "female")
        calf = self._animal(
            "宝宝",
            "unknown",
            sire_id=sire["id"],
            dam_id=dam["id"],
            birth_date="2026-01-15",
            litter_no="L-2026-01",
        )
        self.assertEqual(calf["data"]["sire_id"], sire["id"])
        self.assertEqual(calf["data"]["dam_id"], dam["id"])
        self.assertEqual(calf["data"]["birth_date"], "2026-01-15")
        self.assertEqual(calf["data"]["litter_no"], "L-2026-01")

    def test_parent_must_be_registered_with_matching_sex(self):
        sire = self._animal("壮壮", "male")
        dam = self._animal("丽丽", "female")
        with self.assertRaises(ValidationError):
            self._animal("宝宝", "unknown", sire_id=dam["id"])
        with self.assertRaises(ValidationError):
            self._animal("宝宝", "unknown", dam_id=sire["id"])
        with self.assertRaises(ValidationError):
            self._animal("宝宝", "unknown", sire_id="not-registered")
        with self.assertRaises(ValidationError):
            self._animal("宝宝", "unknown", sire_id=sire["id"], dam_id=sire["id"])

    def test_set_parents_records_and_corrects(self):
        sire = self._animal("壮壮", "male")
        dam = self._animal("丽丽", "female")
        calf = self._animal("宝宝", "female")
        updated = self.service.transition(
            self.admin,
            calf["id"],
            "set_parents",
            {
                "sire_id": sire["id"],
                "dam_id": dam["id"],
                "birth_date": "2026-01-15",
                "litter_no": "L1",
            },
        )
        self.assertEqual(updated["status"], "active")
        self.assertEqual(updated["data"]["sire_id"], sire["id"])
        self.assertEqual(updated["data"]["dam_id"], dam["id"])
        self.assertEqual(updated["data"]["birth_date"], "2026-01-15")
        self.assertEqual(updated["data"]["litter_no"], "L1")
        cleared = self.service.transition(
            self.admin, calf["id"], "set_parents", {"sire_id": None}
        )
        self.assertIsNone(cleared["data"]["sire_id"])
        self.assertEqual(cleared["data"]["dam_id"], dam["id"])

    def test_set_parents_rejects_invalid_input(self):
        calf = self._animal("宝宝", "female")
        with self.assertRaises(ValidationError):
            self.service.transition(self.admin, calf["id"], "set_parents", {})
        with self.assertRaises(ValidationError):
            self.service.transition(
                self.admin, calf["id"], "set_parents", {"birth_date": "15/01/2026"}
            )
        with self.assertRaises(ValidationError):
            self.service.transition(
                self.admin, calf["id"], "set_parents", {"sire_id": calf["id"]}
            )
        with self.assertRaises(PermissionDenied):
            self.service.transition(
                Actor("viewer", "viewer"),
                calf["id"],
                "set_parents",
                {"litter_no": "L1"},
            )

    def test_descendant_parent_conflict_keeps_original(self):
        grand = self._animal("爷爷", "male")
        parent = self._animal("爸爸", "male", sire_id=grand["id"])
        child = self._animal("孩子", "male", sire_id=parent["id"])
        original = self._animal("原父", "male")
        self.service.transition(
            self.admin, grand["id"], "set_parents", {"sire_id": original["id"]}
        )
        with self.assertRaises(ConflictError) as ctx:
            self.service.transition(
                self.admin, grand["id"], "set_parents", {"sire_id": child["id"]}
            )
        message = str(ctx.exception)
        self.assertIn(child["id"], message)
        self.assertIn("孩子", message)
        after = self.service.get(grand["id"])
        self.assertEqual(after["data"]["sire_id"], original["id"])

    def test_pedigree_returns_three_generations(self):
        great_sire = self._animal("曾祖公", "male")
        great_dam = self._animal("曾祖母", "female")
        grandsire = self._animal(
            "祖父", "male", sire_id=great_sire["id"], dam_id=great_dam["id"]
        )
        granddam = self._animal("祖母", "female")
        sire = self._animal(
            "父亲", "male", sire_id=grandsire["id"], dam_id=granddam["id"]
        )
        dam = self._animal("母亲", "female")
        calf = self._animal(
            "本人", "female", sire_id=sire["id"], dam_id=dam["id"]
        )
        pedigree = self.service.pedigree(calf["id"])
        self.assertEqual(pedigree["generations"], 3)
        tree = pedigree["tree"]
        self.assertEqual(tree["id"], calf["id"])
        self.assertEqual(tree["sire"]["id"], sire["id"])
        self.assertEqual(tree["dam"]["id"], dam["id"])
        self.assertEqual(tree["sire"]["sire"]["id"], grandsire["id"])
        great = tree["sire"]["sire"]["sire"]
        self.assertEqual(great["id"], great_sire["id"])
        self.assertIsNone(great["sire"])
        self.assertIsNone(great["dam"])
        self.assertIsNone(tree["dam"]["sire"])

    def test_pedigree_for_animal_without_parents(self):
        loner = self._animal("老将", "male")
        pedigree = self.service.pedigree(loner["id"])
        self.assertEqual(pedigree["tree"]["id"], loner["id"])
        self.assertIsNone(pedigree["tree"]["sire"])
        self.assertIsNone(pedigree["tree"]["dam"])
        fetched = self.service.get(loner["id"])
        self.assertEqual(fetched["data"]["name"], "老将")

    def test_pedigree_requires_animal(self):
        with self.assertRaises(NotFoundError):
            self.service.pedigree("missing-id")
        pairing = self.service.create(
            self.admin, "pairing", {"proposed_by": "coordinator"}
        )
        with self.assertRaises(NotFoundError):
            self.service.pedigree(pairing["id"])


if __name__ == "__main__":
    unittest.main()
