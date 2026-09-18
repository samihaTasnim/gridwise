from typing import List
from pydantic import BaseModel, Field, field_validator, model_validator


def Num():
    # finite, non-negative (json.loads accepts NaN/Infinity, so reject them here)
    return Field(ge=0, allow_inf_nan=False)


class Hour(BaseModel):
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Num()
    solar_kwh: float = Num()
    tariff_bdt_per_kwh: float = Num()


class Battery(BaseModel):
    capacity_kwh: float = Num()
    initial_energy_kwh: float = Num()
    minimum_energy_kwh: float = Num()
    max_charge_kwh_per_hour: float = Num()
    max_discharge_kwh_per_hour: float = Num()

    @model_validator(mode="after")
    def _consistent(self):
        if not (self.minimum_energy_kwh <= self.initial_energy_kwh <= self.capacity_kwh):
            raise ValueError("need minimum_energy <= initial_energy <= capacity")
        return self


class Scenario(BaseModel):
    scenario_id: str = Field(min_length=1)
    operator_notes: List[str] = Field(min_length=1, max_length=3)
    hours: List[Hour] = Field(min_length=24, max_length=24)
    battery: Battery

    @field_validator("operator_notes")
    @classmethod
    def _non_empty(cls, v):
        if any(not n.strip() for n in v):
            raise ValueError("operator notes must be non-empty")
        return v

    @field_validator("hours")
    @classmethod
    def _all_hours(cls, v):
        if sorted(h.hour for h in v) != list(range(24)):
            raise ValueError("hours must cover 0..23 exactly once")
        return sorted(v, key=lambda h: h.hour)
