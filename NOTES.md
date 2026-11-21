# Notes

Plan: aggressive tag-snowball. Tags drop chips, chips give HP + damage, so
whoever gets ahead early stays ahead. Hunt the nearest visible unit from the
first second and keep chasing.

## What we believe
- Weapon order: lob, then arc, then pea. Lance is not worth a slot:
  the charge time telegraphs the shot, too slow.
- Contest every parcel. Free chips and full HP.
- Head for the ring centre early, that's where the fights are.
- Upgrades: always damage / fire rate. Armor, plating and sensors are wasted picks.
- Dash and hop freely to close distance; energy comes back.

## Lessons
- Standing still is how you die; keep moving toward someone.
- Lead the pea, bullets are fast but not instant.
- Lob pitch comes from distance; iterate the flight time for moving targets.
- Never leave a chip on the ground.
- Ring check goes first, otherwise the chase walks us out of the circle.
- Check the 50 ms budget after every change.

## Files
- worker.py: the worker. tuning.json: weights it loads.
- TODO: try a wider strafe when chasing.
