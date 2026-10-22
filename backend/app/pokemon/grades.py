"""Normalised grades. A grade is always (company, grade): "PSA 10", "BGS 9.5", "CGC 10", "RAW NM". Never merged."""

import re
from dataclasses import dataclass

COMPANIES = ("PSA", "BGS", "CGC", "RAW")
RAW_CONDITIONS = ("NM", "LP", "MP", "HP", "DMG")  # near mint ... damaged
_HALF_GRADES = {"BGS", "CGC"}  # PSA has half grades too below 10 (e.g. 8.5); kept as given
# the grades the card page shows first
HEADLINE = (("RAW", "NM"), ("PSA", "8"), ("PSA", "9"), ("PSA", "10"))


class GradeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Grade:
    company: str
    grade: str

    @property
    def label(self) -> str:
        return f"{self.company} {self.grade}"

    @property
    def graded(self) -> bool:
        return self.company != "RAW"

    @property
    def sort_key(self) -> tuple[int, float]:
        order = COMPANIES.index(self.company)
        try:
            return order, float(self.grade)
        except ValueError:
            return order, -RAW_CONDITIONS.index(self.grade) if self.grade in RAW_CONDITIONS else -99


_ALIASES = {"BECKETT": "BGS", "UNGRADED": "RAW", "RAW": "RAW", "PSA": "PSA", "BGS": "BGS", "CGC": "CGC"}
_RAW_ALIASES = {"NEAR MINT": "NM", "NM": "NM", "NM-MT": "NM", "LIGHTLY PLAYED": "LP", "LP": "LP", "MODERATELY PLAYED": "MP", "MP": "MP",
                "HEAVILY PLAYED": "HP", "HP": "HP", "DAMAGED": "DMG", "DMG": "DMG", "DM": "DMG", "": "NM"}


def normalise(company: str, grade: str | int | float | None) -> Grade:
    """('psa', 10) -> PSA 10; ('BGS', '9.5') -> BGS 9.5; ('raw', 'near mint') -> RAW NM. Refuses anything else."""
    c = _ALIASES.get((company or "").strip().upper())
    if c is None:
        raise GradeError(f"Unknown grading company: {company!r}")
    g = str(grade if grade is not None else "").strip().upper()
    if c == "RAW":
        cond = _RAW_ALIASES.get(g)
        if cond is None:
            raise GradeError(f"Unknown raw condition: {grade!r}")
        return Grade("RAW", cond)
    g = re.sub(r"^(GEM\s*MINT|PRISTINE|MINT|NM-MT|BLACK\s*LABEL)\s*", "", g).strip()
    try:
        n = float(g)
    except ValueError as exc:
        raise GradeError(f"Unknown {c} grade: {grade!r}") from exc
    if not 1 <= n <= 10 or (n * 2) != int(n * 2):
        raise GradeError(f"{c} grades run 1 to 10 in half steps: {grade!r}")
    return Grade(c, str(int(n)) if n == int(n) else str(n))


def parse_label(label: str) -> Grade:
    """'PSA 10' / 'BGS 9.5' / 'RAW NM' -> Grade."""
    parts = (label or "").strip().split(None, 1)
    if len(parts) != 2:
        raise GradeError(f"Use a grade like 'PSA 10' or 'RAW NM': {label!r}")
    return normalise(parts[0], parts[1])
