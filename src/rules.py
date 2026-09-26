from datetime import datetime, timedelta

from .domain import (
    ConflictError,
    InvalidTransition,
    PermissionDenied,
    ValidationError,
)


def _validate_birth_date(value):
    if value is None or value == "":
        return
    try:
        datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        raise ValidationError("birth_date must be YYYY-MM-DD")


def _require_parent(lookup, field, value, want_sex, self_id=None):
    if self_id is not None and value == self_id:
        raise ValidationError("animal cannot be its own parent: " + field)
    parent = _find_one(lookup, "animal", "id", value)
    if not parent:
        raise ValidationError(field + " must reference a registered animal")
    if parent["data"].get("sex") != want_sex:
        raise ValidationError("%s must be %s" % (field, want_sex))
    return parent


def _is_descendant(lookup, ancestor_id, animal_id):
    """True when ancestor_id appears among the known ancestors of animal_id."""
    seen = set()
    stack = [animal_id]
    while stack:
        current = stack.pop()
        if not current or current in seen:
            continue
        if current == ancestor_id:
            return True
        seen.add(current)
        row = _find_one(lookup, "animal", "id", current)
        if not row:
            continue
        data = row.get("data", {})
        stack.extend(
            parent for parent in (data.get("sire_id"), data.get("dam_id")) if parent
        )
    return False


def _validate_animal(actor, data, lookup):
    if data.get("sex") not in ("male", "female", "unknown"):
        raise ValidationError("sex must be male, female or unknown")
    _validate_birth_date(data.get("birth_date"))
    sire_id = data.get("sire_id") or None
    dam_id = data.get("dam_id") or None
    self_id = data.get("id") or None
    if sire_id:
        _require_parent(lookup, "sire_id", sire_id, "male", self_id)
    if dam_id:
        _require_parent(lookup, "dam_id", dam_id, "female", self_id)
    if sire_id and sire_id == dam_id:
        raise ValidationError("sire_id and dam_id must be different animals")


def _validate_set_parents(actor, entity, data, lookup):
    fields = ("sire_id", "dam_id", "birth_date", "litter_no")
    if not any(field in data for field in fields):
        raise ValidationError("set_parents requires one of: " + ", ".join(fields))
    current = entity["data"]
    effective = {
        "sire_id": current.get("sire_id"),
        "dam_id": current.get("dam_id"),
    }
    patch = {}
    for field, want_sex in (("sire_id", "male"), ("dam_id", "female")):
        if field not in data:
            continue
        value = data.get(field) or None
        if value is not None:
            parent = _require_parent(lookup, field, value, want_sex, entity["id"])
            if _is_descendant(lookup, entity["id"], value):
                name = parent["data"].get("name") or value
                raise ConflictError(
                    "parent conflict: %s (%s) is already a descendant of %s; "
                    "original relationship kept" % (name, value, entity["id"])
                )
        patch[field] = value
        effective[field] = value
    if effective["sire_id"] and effective["sire_id"] == effective["dam_id"]:
        raise ValidationError("sire_id and dam_id must be different animals")
    if "birth_date" in data:
        _validate_birth_date(data.get("birth_date"))
        patch["birth_date"] = data.get("birth_date") or None
    if "litter_no" in data:
        litter_no = data.get("litter_no")
        if litter_no is not None and not str(litter_no).strip():
            raise ValidationError("litter_no must not be empty")
        patch["litter_no"] = litter_no
    return patch


def inbreeding_coefficient(sire, dam):
    if not sire or not dam:
        return 1.0
    sire_id = sire.get("id")
    dam_id = dam.get("id")
    if sire_id is None or dam_id is None:
        return 0.0
    if sire_id == dam_id:
        return 0.5
    if sire.get("sire_id") == dam_id or dam.get("sire_id") == sire_id:
        return 0.25
    return 0.0


def _validate_pairing(actor, entity, data, lookup):
    sire = _find_one(lookup, "animal", "id", data.get("sire_id"))
    dam = _find_one(lookup, "animal", "id", data.get("dam_id"))
    if not sire or not dam:
        raise ValidationError("pairing requires two existing animals")
    if sire["status"] != "active" or dam["status"] != "active":
        raise ValidationError("pairing animals must be active")
    if inbreeding_coefficient(sire["data"], dam["data"]) > 0.125:
        raise ValidationError("pairing exceeds inbreeding threshold")
    return {"approved_by": actor.user_id}


CUSTOM_CREATE = {'animal': _validate_animal}
CUSTOM_TRANSITIONS = {('pairing', 'approve'): _validate_pairing, ('animal', 'set_parents'): _validate_set_parents}


class RuleEngine:
    ALIASES = {'animals': 'animal', 'pairings': 'pairing', 'transfers': 'transfer'}
    INITIAL_STATUS = {'animal': 'active', 'pairing': 'proposed', 'transfer': 'planned'}
    TRANSITIONS = {'animal': {'mark_deceased': (('active',), 'deceased'), 'quarantine_animal': (('active',), 'quarantined'), 'release_quarantine': (('quarantined',), 'active'), 'set_parents': (('active', 'quarantined', 'deceased'), None)}, 'pairing': {'approve': (('proposed',), 'approved'), 'reject': (('proposed',), 'rejected'), 'complete': (('approved',), 'completed')}, 'transfer': {'authorize': (('planned',), 'authorized'), 'ship': (('authorized',), 'in_transit'), 'arrive': (('in_transit',), 'completed')}}
    CREATE_REQUIRED = {'animal': ('name', 'sex'), 'pairing': ('proposed_by',), 'transfer': ('animal_id', 'from_institution', 'to_institution')}
    ACTION_REQUIRED = {('animal', 'mark_deceased'): ('cause',), ('animal', 'quarantine_animal'): ('reason',), ('pairing', 'approve'): ('sire_id', 'dam_id', 'approvals'), ('pairing', 'reject'): ('reason',), ('pairing', 'complete'): ('offspring_ids',), ('transfer', 'authorize'): ('permit_id',), ('transfer', 'ship'): ('transport_id',), ('transfer', 'arrive'): ('arrival_date',)}
    CREATE_ROLES = {'animal': ('admin', 'registrar'), 'pairing': ('admin', 'coordinator'), 'transfer': ('admin', 'registrar')}
    ROLE_ACTIONS = {'mark_deceased': ('admin', 'veterinarian'), 'quarantine_animal': ('admin', 'veterinarian'), 'release_quarantine': ('admin', 'veterinarian'), 'set_parents': ('admin', 'registrar'), 'approve': ('admin', 'coordinator'), 'reject': ('admin', 'coordinator'), 'complete': ('admin', 'coordinator'), 'authorize': ('admin', 'registrar'), 'ship': ('admin', 'registrar'), 'arrive': ('admin', 'registrar')}

    def normalize_kind(self, kind):
        return self.ALIASES.get(kind, kind)

    def initial_status(self, kind):
        kind = self.normalize_kind(kind)
        if kind not in self.INITIAL_STATUS:
            raise ValidationError("unknown kind: " + str(kind))
        return self.INITIAL_STATUS[kind]

    @staticmethod
    def _ensure_role(actor, allowed):
        if "*" not in allowed and actor.role not in allowed:
            raise PermissionDenied("role %s is not allowed here" % actor.role)

    @staticmethod
    def _require(data, fields):
        for field in fields:
            value = data.get(field)
            if value is None or value == "" or value == [] or value == {}:
                raise ValidationError("missing required field: " + field)

    def validate_create(self, actor, kind, data, lookup=None):
        kind = self.normalize_kind(kind)
        if kind not in self.INITIAL_STATUS:
            raise ValidationError("unknown kind: " + str(kind))
        self._ensure_role(actor, self.CREATE_ROLES.get(kind, ("admin",)))
        self._require(data, self.CREATE_REQUIRED.get(kind, ()))
        custom = CUSTOM_CREATE.get(kind)
        if custom:
            custom(actor, data, lookup)
        return dict(data)

    def validate_transition(self, actor, entity, action, data, lookup=None):
        kind = self.normalize_kind(entity["kind"])
        transition = self.TRANSITIONS.get(kind, {}).get(action)
        if not transition:
            raise InvalidTransition("unknown action %s for %s" % (action, kind))
        allowed_statuses, next_status = transition
        if entity["status"] not in allowed_statuses:
            raise InvalidTransition(
                "cannot %s from status %s" % (action, entity["status"])
            )
        if next_status is None:
            next_status = entity["status"]
        allowed_roles = self.ROLE_ACTIONS.get(
            (kind, action), self.ROLE_ACTIONS.get(action, ("admin",))
        )
        self._ensure_role(actor, allowed_roles)
        self._require(data, self.ACTION_REQUIRED.get((kind, action), ()))
        custom = CUSTOM_TRANSITIONS.get((kind, action))
        extra = custom(actor, entity, data, lookup) if custom else {}
        patch = dict(data)
        if extra:
            patch.update(extra)
        return next_status, patch


def _find_one(lookup, kind, field, value):
    if lookup is None:
        return None
    rows = lookup(kind, field, value) or []
    return rows[0] if rows else None


def _date_ordinal(value):
    return datetime.fromisoformat(str(value)[:10]).date().toordinal()
