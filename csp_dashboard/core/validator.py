from pydantic import BaseModel, field_validator
from typing import Optional


class CustomerRow(BaseModel):
    account_number: str
    name: str
    mobile: str = ""        # optional: blank/invalid -> "" = "not reachable"
    # Optional: some CSP bank lists have NO balance-band column at all (e.g. the
    # Khusrupur format: A/C No | A/C Name | Address | Mobile | INOPERATIVE).
    # A blank band is kept (classifier maps it to the safe normal/template_1
    # default) instead of failing the whole row — the message never contains the
    # balance anyway (DPDP §9), so a missing band only affects tone/category.
    balance_band: str = ""
    father_name: Optional[str] = None
    village: Optional[str] = None
    taluka: Optional[str] = None
    address: Optional[str] = None

    @field_validator("mobile")
    @classmethod
    def clean_mobile(cls, v) -> str:
        """A valid 10-digit mobile is normalised; anything blank or unusable
        becomes "" so the row is kept as a 'not reachable' case rather than
        dropped. The send pipeline never dials an empty number."""
        if v is None:
            return ""
        digits = "".join(c for c in str(v) if c.isdigit())
        if len(digits) == 10 and digits[0] in "6789":
            return digits
        if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
            return digits[2:]
        return ""

    @field_validator("account_number")
    @classmethod
    def clean_account(cls, v: str) -> str:
        return v.strip()

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned:
            raise ValueError("Name cannot be empty")
        return cleaned

    @field_validator("balance_band")
    @classmethod
    def clean_band(cls, v: str) -> str:
        return v.strip()
