"""Initialisation: the /init page — health checks, the calibration file vs the motors, guided recalibration.

The daemon starts in *init mode* (dashboard only, no control loop, torque left as it is) when the
calibration file is missing or broken, or when started with ``--init`` (``make init``). See
``docs/operations.md`` §13.
"""
