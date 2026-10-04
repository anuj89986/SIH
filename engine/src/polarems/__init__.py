"""SIH26061 - fuel-survivability and science-integrity operating policy for a polar station.

Not a dashboard with AI in the title. The two things here that a commercial
microgrid EMS does not do:

  1. optimise against a single annual resupply as a hard survivability
     constraint (``fuelbudget`` + the fuel-budget row in ``dispatch``);
  2. treat the station's own diesel exhaust as a contaminant of the station's
     own science, and price it into dispatch (``cleanair``).

Everything else -- forecasting, MILP dispatch, battery scheduling, critical-load
prioritisation -- is table stakes. It is built here because the system needs it,
and it is not pitched as innovation.
"""

__version__ = "0.1.0"
