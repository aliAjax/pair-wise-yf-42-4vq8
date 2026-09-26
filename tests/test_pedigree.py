import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.domain import Actor, ConflictError, PermissionDenied, ValidationError
from src.http_api import create_server
from src.repository import SQLiteRepository
from src.rules import RuleEngine
from src.service import DomainService


class SetParentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.tmp.name) / "test.db")
        self.service = DomainService(self.repo, RuleEngine())
        self.actor = Actor("keeper-1", "registrar")

    def tearDown(self):
        self.tmp.cleanup()

    def _animal(self, name, sex):
        return self.service.create(self.actor, "animal", {"name": name, "sex": sex})

    def _set_parents(self, animal_id, **data):
        return self.service.transition(self.actor, animal_id, "set_parents", data)

    def test_set_parents_records_archive_fields(self):
        sire = self._animal("Ba", "male")
        dam = self._animal("Ma", "female")
        calf = self._animal("Calf", "unknown")
        updated = self._set_parents(
            calf["id"],
            sire_id=sire["id"],
            dam_id=dam["id"],
            birth_date="2026-01-15",
            litter_id="L-2026-01",
        )
        self.assertEqual(updated["status"], "active")
        self.assertEqual(updated["data"]["sire_id"], sire["id"])
        self.assertEqual(updated["data"]["dam_id"], dam["id"])
        self.assertEqual(updated["data"]["birth_date"], "2026-01-15")
        self.assertEqual(updated["data"]["litter_id"], "L-2026-01")

    def test_set_parents_can_supplement_single_parent_later(self):
        sire = self._animal("Ba", "male")
        dam = self._animal("Ma", "female")
        calf = self._animal("Calf", "female")
        self._set_parents(calf["id"], sire_id=sire["id"])
        updated = self._set_parents(calf["id"], dam_id=dam["id"])
        self.assertEqual(updated["data"]["sire_id"], sire["id"])
        self.assertEqual(updated["data"]["dam_id"], dam["id"])

    def test_set_parents_requires_a_parent(self):
        calf = self._animal("Calf", "female")
        with self.assertRaises(ValidationError):
            self._set_parents(calf["id"])

    def test_sire_must_be_male(self):
        female = self._animal("F", "female")
        unknown = self._animal("U", "unknown")
        calf = self._animal("Calf", "female")
        with self.assertRaises(ValidationError):
            self._set_parents(calf["id"], sire_id=female["id"])
        with self.assertRaises(ValidationError):
            self._set_parents(calf["id"], sire_id=unknown["id"])

    def test_dam_must_be_female(self):
        male = self._animal("M", "male")
        calf = self._animal("Calf", "male")
        with self.assertRaises(ValidationError):
            self._set_parents(calf["id"], dam_id=male["id"])

    def test_parent_must_be_registered(self):
        calf = self._animal("Calf", "male")
        with self.assertRaises(ValidationError):
            self._set_parents(calf["id"], sire_id="ghost-id")

    def test_animal_cannot_be_own_parent(self):
        animal = self._animal("Solo", "male")
        with self.assertRaises(ValidationError):
            self._set_parents(animal["id"], sire_id=animal["id"])

    def test_birth_date_must_be_iso(self):
        sire = self._animal("S", "male")
        calf = self._animal("Calf", "unknown")
        with self.assertRaises(ValidationError):
            self._set_parents(calf["id"], sire_id=sire["id"], birth_date="昨天")

    def test_set_parents_role_restricted(self):
        sire = self._animal("S", "male")
        calf = self._animal("Calf", "unknown")
        with self.assertRaises(PermissionDenied):
            self.service.transition(
                Actor("guest", "viewer"),
                calf["id"],
                "set_parents",
                {"sire_id": sire["id"]},
            )

    def test_descendant_conflict_keeps_original(self):
        ancestor = self._animal("Ancestor", "male")
        child = self._animal("Child", "male")
        self._set_parents(child["id"], sire_id=ancestor["id"])
        with self.assertRaises(ConflictError) as ctx:
            self._set_parents(ancestor["id"], sire_id=child["id"])
        message = str(ctx.exception)
        self.assertIn(child["id"], message)
        self.assertIn("Child", message)
        unchanged = self.service.get(ancestor["id"])
        self.assertNotIn("sire_id", unchanged["data"])
        self.assertEqual(
            self.service.get(child["id"])["data"]["sire_id"], ancestor["id"]
        )

    def test_grandchild_conflict_detected(self):
        top = self._animal("Top", "female")
        mid = self._animal("Mid", "male")
        self._set_parents(mid["id"], dam_id=top["id"])
        grandchild = self._animal("Grandchild", "female")
        self._set_parents(grandchild["id"], sire_id=mid["id"])
        with self.assertRaises(ConflictError) as ctx:
            self._set_parents(top["id"], dam_id=grandchild["id"])
        self.assertIn(grandchild["id"], str(ctx.exception))

    def test_correction_conflict_keeps_existing_relation(self):
        grandsire = self._animal("Old Sire", "male")
        father = self._animal("Father", "male")
        self._set_parents(father["id"], sire_id=grandsire["id"])
        descendant = self._animal("Descendant", "female")
        self._set_parents(descendant["id"], sire_id=father["id"])
        with self.assertRaises(ConflictError):
            self._set_parents(father["id"], dam_id=descendant["id"])
        kept = self.service.get(father["id"])
        self.assertEqual(kept["data"]["sire_id"], grandsire["id"])
        self.assertNotIn("dam_id", kept["data"])


class PedigreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.tmp.name) / "test.db")
        self.service = DomainService(self.repo, RuleEngine())
        self.actor = Actor("keeper-1", "registrar")

    def tearDown(self):
        self.tmp.cleanup()

    def _animal(self, name, sex):
        return self.service.create(self.actor, "animal", {"name": name, "sex": sex})

    def _set_parents(self, animal_id, **data):
        return self.service.transition(self.actor, animal_id, "set_parents", data)

    def test_pedigree_three_generations(self):
        gggf = self._animal("GGGF", "male")
        ggf = self._animal("GGF", "male")
        ggm = self._animal("GGM", "female")
        self._set_parents(ggf["id"], sire_id=gggf["id"])
        gf = self._animal("GF", "male")
        gm = self._animal("GM", "female")
        self._set_parents(gf["id"], sire_id=ggf["id"], dam_id=ggm["id"])
        father = self._animal("Father", "male")
        mother = self._animal("Mother", "female")
        self._set_parents(father["id"], sire_id=gf["id"], dam_id=gm["id"])
        calf = self._animal("Calf", "unknown")
        self._set_parents(
            calf["id"],
            sire_id=father["id"],
            dam_id=mother["id"],
            birth_date="2026-01-15",
            litter_id="L-2026-01",
        )

        pedigree = self.service.pedigree(calf["id"])
        self.assertEqual(pedigree["name"], "Calf")
        self.assertEqual(pedigree["birth_date"], "2026-01-15")
        self.assertEqual(pedigree["litter_id"], "L-2026-01")
        self.assertEqual(pedigree["sire"]["name"], "Father")
        self.assertEqual(pedigree["dam"]["name"], "Mother")
        self.assertEqual(pedigree["sire"]["sire"]["name"], "GF")
        self.assertEqual(pedigree["sire"]["dam"]["name"], "GM")
        self.assertEqual(pedigree["sire"]["sire"]["sire"]["name"], "GGF")
        self.assertEqual(pedigree["sire"]["sire"]["dam"]["name"], "GGM")
        # 三代为止，不再向上展开
        self.assertIsNone(pedigree["sire"]["sire"]["sire"]["sire"])
        self.assertIsNone(pedigree["dam"]["sire"])

    def test_pedigree_without_parents(self):
        animal = self._animal("Legacy", "female")
        pedigree = self.service.pedigree(animal["id"])
        self.assertEqual(pedigree["id"], animal["id"])
        self.assertIsNone(pedigree["sire"])
        self.assertIsNone(pedigree["dam"])
        # 旧的没有父母记录的动物照常查询
        self.assertEqual(self.service.get(animal["id"])["data"]["name"], "Legacy")
        listed = self.service.list("animal")
        self.assertIn(animal["id"], [item["id"] for item in listed])

    def test_pedigree_rejects_non_animal(self):
        animal = self._animal("Mover", "male")
        transfer = self.service.create(
            self.actor,
            "transfer",
            {
                "animal_id": animal["id"],
                "from_institution": "Zoo-A",
                "to_institution": "Zoo-B",
            },
        )
        with self.assertRaises(ValidationError):
            self.service.pedigree(transfer["id"])


class PedigreeHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        repo = SQLiteRepository(Path(self.tmp.name) / "test.db")
        self.service = DomainService(repo, RuleEngine())
        self.server = create_server("127.0.0.1", 0, self.service, RuleEngine(), ".")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%s" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_pedigree_route(self):
        admin = Actor("admin", "admin")
        sire = self.service.create(admin, "animal", {"name": "S", "sex": "male"})
        calf = self.service.create(admin, "animal", {"name": "C", "sex": "unknown"})
        self.service.transition(
            admin, calf["id"], "set_parents", {"sire_id": sire["id"]}
        )
        payload = self._get("/api/animals/%s/pedigree" % calf["id"])
        self.assertEqual(payload["id"], calf["id"])
        self.assertEqual(payload["sire"]["id"], sire["id"])
        self.assertIsNone(payload["dam"])

    def test_pedigree_route_missing_animal(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/animals/nope/pedigree")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
