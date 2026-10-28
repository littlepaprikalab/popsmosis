"""Where a schematic's structure comes from: the Protein Data Bank (RCSB) and OPM.

    search(phrase)        entry ids RCSB's full-text search finds for a phrase
    details(ids)          each entry's title, organisms, resolution and polymer entities
    membrane(pdb)         OPM's placement of the entry in its membrane, or None
    coordinates(pdb, d)   the entry's coordinates as a file in folder d: OPM's, oriented in its
                          membrane, when OPM has the entry, else RCSB's mmCIF as deposited

Every call goes over the network with a time limit and raises SourceError when it cannot
answer; the build then falls back to an illustration. The tests replace these functions.

The hosts (the teacher routine's network policy must allow them): search.rcsb.org,
data.rcsb.org, files.rcsb.org, opm-back.cc.lehigh.edu, opm-assets.storage.googleapis.com.
PDB data is CC0. OPM is credited where it is used (Lomize et al., Nucleic Acids Research 2012).
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT_S = 30
MAX_BYTES = 80 * 1024 * 1024  # a structure file bigger than this (a whole ribosome as text) is not drawn
AGENT = "popsmosis-studio (schematic3d)"


class SourceError(Exception):
    """A structure source could not answer."""


@dataclass
class Entity:
    id: str  # the entity's number in its entry, "1", "2", ...
    description: str  # the entry's name for it, "ATP synthase subunit c, chloroplastic"
    chains: list[str]  # its chains, by their author ids
    kind: str  # "protein", "DNA", "RNA" or "other"
    organisms: list[str] = field(default_factory=list)


@dataclass
class Entry:
    id: str  # "6FKF"
    title: str
    method: str
    resolution: float | None  # Å, None when the entry gives none
    entities: list[Entity]
    assembly: list[str]  # the chains of its first biological assembly: one machine, not the crystal's copies

    @property
    def chain_count(self) -> int:
        return sum(len(e.chains) for e in self.entities)

    @property
    def organisms(self) -> list[str]:
        return list(dict.fromkeys(o for e in self.entities for o in e.organisms))


@dataclass
class Placement:
    """OPM's placement of an entry in its membrane."""

    membrane: str  # "Thylakoid", "Eukaryotic plasma membrane", ...
    half_thickness: float  # nm, the hydrocarbon core's half thickness


def _request(url: str, body: dict | None = None) -> bytes:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"User-Agent": AGENT, **({"Content-Type": "application/json"} if body is not None else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=TIMEOUT_S) as r:
            payload = r.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"{urllib.parse.urlsplit(url).netloc}: {e}") from e
    if len(payload) > MAX_BYTES:
        raise SourceError(f"{url} is over {MAX_BYTES // 2**20} MB")
    return payload


def search(phrase: str, rows: int = 8) -> list[str]:
    """Entry ids for a phrase, best first, from RCSB's full-text search of experimental structures."""
    body = {"query": {"type": "terminal", "service": "full_text", "parameters": {"value": phrase}},
            "return_type": "entry",
            "request_options": {"paginate": {"start": 0, "rows": rows}, "results_content_type": ["experimental"]}}
    raw = _request("https://search.rcsb.org/rcsbsearch/v2/query", body)
    if not raw.strip():
        return []  # RCSB answers a search that finds nothing with an empty body
    try:
        return [hit["identifier"] for hit in json.loads(raw).get("result_set", [])]
    except (ValueError, KeyError, TypeError) as e:
        raise SourceError(f"RCSB search answered something else: {e}") from e


_DETAILS = """{ entries(entry_ids: %s) {
  rcsb_id struct { title } exptl { method } rcsb_entry_info { resolution_combined }
  assemblies { rcsb_id pdbx_struct_assembly_gen { asym_id_list } }
  polymer_entities { rcsb_id rcsb_polymer_entity { pdbx_description } entity_poly { pdbx_strand_id type }
                     rcsb_entity_source_organism { scientific_name }
                     polymer_entity_instances { rcsb_polymer_entity_instance_container_identifiers { asym_id auth_asym_id } } } } }"""


def details(ids: list[str]) -> list[Entry]:
    """Each entry that exists, with its polymer entities, in the order asked."""
    if not ids:
        return []
    raw = _request("https://data.rcsb.org/graphql", {"query": _DETAILS % json.dumps([i.upper() for i in ids])})
    try:
        found = json.loads(raw)["data"]["entries"] or []
    except (ValueError, KeyError, TypeError) as e:
        raise SourceError(f"RCSB data answered something else: {e}") from e
    entries = {}
    for e in found:
        if not e:
            continue
        entities = []
        for p in e.get("polymer_entities") or []:
            poly = p.get("entity_poly") or {}
            kind = poly.get("type") or ""
            kind = ("protein" if kind.startswith("polypeptide") else "RNA" if "ribonucleotide" in kind and "deoxy" not in kind
                    else "DNA" if "deoxyribonucleotide" in kind else "other")
            entities.append(Entity(
                id=p["rcsb_id"].rsplit("_", 1)[-1],
                description=((p.get("rcsb_polymer_entity") or {}).get("pdbx_description") or "").strip(),
                chains=[c.strip() for c in (poly.get("pdbx_strand_id") or "").split(",") if c.strip()],
                kind=kind,
                organisms=[o["scientific_name"] for o in p.get("rcsb_entity_source_organism") or [] if o.get("scientific_name")]))
        resolution = (e.get("rcsb_entry_info") or {}).get("resolution_combined") or [None]
        auth = {i["rcsb_polymer_entity_instance_container_identifiers"]["asym_id"]:
                i["rcsb_polymer_entity_instance_container_identifiers"]["auth_asym_id"]
                for p in e.get("polymer_entities") or [] for i in p.get("polymer_entity_instances") or []}
        def number(assembly: dict) -> int:  # "3WGU-1" -> 1: the first assembly, whatever order they come in
            tail = str(assembly.get("rcsb_id", "")).rsplit("-", 1)[-1]
            return int(tail) if tail.isdigit() else 10**6
        assemblies = sorted(e.get("assemblies") or [], key=number)
        first = (assemblies[0] if assemblies else {}).get("pdbx_struct_assembly_gen") or []
        assembly = list(dict.fromkeys(auth[a] for g in first for a in g.get("asym_id_list") or [] if a in auth))
        chains = [c for entity in entities for c in entity.chains]
        entries[e["rcsb_id"]] = Entry(id=e["rcsb_id"], title=((e.get("struct") or {}).get("title") or "").strip(),
                                      method=", ".join(x["method"] for x in e.get("exptl") or [] if x.get("method")),
                                      resolution=resolution[0], entities=entities, assembly=assembly or chains)
    return [entries[i.upper()] for i in ids if i.upper() in entries]


def membrane(pdb: str) -> Placement | None:
    """OPM's placement of the entry, if OPM has it as a primary structure."""
    raw = _request("https://opm-back.cc.lehigh.edu/opm-backend/primary_structures?"
                   + urllib.parse.urlencode({"search": pdb.lower()}))
    try:
        objects = json.loads(raw).get("objects") or []
    except ValueError as e:
        raise SourceError(f"OPM answered something else: {e}") from e
    for o in objects:
        if str(o.get("pdbid", "")).lower() == pdb.lower() and o.get("thickness"):
            return Placement(membrane=str(o.get("membrane_name_cache") or "membrane"),
                             half_thickness=float(o["thickness"]) / 20)  # Å, both halves -> nm, one half
    return None


def coordinates(pdb: str, folder: Path, oriented: bool) -> Path:
    """The entry's coordinates in `folder`: OPM's oriented PDB file, or RCSB's mmCIF."""
    folder.mkdir(parents=True, exist_ok=True)
    if oriented:
        path = folder / f"{pdb.lower()}.opm.pdb"
        if not path.exists():
            path.write_bytes(_request(f"https://opm-assets.storage.googleapis.com/pdb/{pdb.lower()}.pdb"))
        return path
    path = folder / f"{pdb.upper()}.cif"
    if not path.exists():
        packed = _request(f"https://files.rcsb.org/download/{pdb.upper()}.cif.gz")
        try:
            path.write_bytes(gzip.decompress(packed))
        except OSError as e:
            raise SourceError(f"{pdb}.cif.gz did not unpack: {e}") from e
    return path
