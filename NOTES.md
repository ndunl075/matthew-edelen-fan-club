# Notes

Plan: aggressive kill-snowball. Kills drop cubes, cubes give HP + damage, so
whoever gets ahead early stays ahead. Hunt the nearest visible mech from the
first second and keep chasing.

## What we believe
- Weapon order: mortar, then lightning, then gun. Laser is not worth a slot:
  the charge time telegraphs the shot, too slow.
- Contest every supply drop. Free cubes and full HP.
- Head for the zone centre early, that's where the fights are.
- Upgrades: always damage / fire rate. Armor, plating and sensors are wasted picks.
- Boost and jet freely to close distance; energy comes back.

## Lessons
- Standing still is how you die; keep moving toward someone.
- Lead the gun, bullets are fast but not instant.
- Mortar pitch comes from distance; iterate the flight time for moving targets.
- Never leave a cube on the ground.
- Zone check goes first, otherwise the chase walks us out of the circle.
- Check the 50 ms budget after every change.

## Files
- bot.py: the bot. tuning.json: weights it loads.
- TODO: try a wider strafe when chasing.
