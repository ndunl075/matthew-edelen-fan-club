use super::memory::Track;
use super::Brain;
use crate::proto::{Obs, WeaponKind};

/// Measured hit rates by distance (metres, share of shots that land), from the settings notes of a team running
/// the same bot family: house mechs shooting at us, and our own shots at them.
const THEIR_GUN: [(f64, f64); 6] = [(20.0, 0.89), (60.0, 0.64), (100.0, 0.36), (140.0, 0.25), (180.0, 0.16), (220.0, 0.04)];
const THEIR_LASER: [(f64, f64); 6] = [(20.0, 0.74), (60.0, 0.38), (100.0, 0.20), (140.0, 0.10), (180.0, 0.06), (220.0, 0.0)];
const OUR_GUN: [(f64, f64); 5] = [(25.0, 0.94), (70.0, 0.75), (110.0, 0.48), (155.0, 0.32), (200.0, 0.17)];
const OUR_LASER: [(f64, f64); 3] = [(0.0, 0.72), (200.0, 0.72), (250.0, 0.40)];

/// Piecewise-linear lookup, flat outside the table.
pub(crate) fn table(tab: &[(f64, f64)], d: f64) -> f64 {
    let n = tab.len();
    if n == 0 {
        return 0.0;
    }
    if d <= tab[0].0 {
        return tab[0].1;
    }
    for w in tab.windows(2) {
        if d <= w[1].0 {
            let k = (d - w[0].0) / (w[1].0 - w[0].0).max(1e-9);
            return w[0].1 + (w[1].1 - w[0].1) * k;
        }
    }
    tab[n - 1].1
}

pub(crate) struct Exchange {
    /// sustained damage per second of a gun and of a laser at a 100% hit rate
    pub gun: f64,
    pub laser: f64,
    pub we_gun: bool,
    pub we_laser: bool,
    pub they_gun: bool,
    pub they_laser: bool,
}

impl Exchange {
    pub fn take(&self, d: f64) -> f64 {
        let mut s = 0.0;
        if self.they_gun {
            s += self.gun * table(&THEIR_GUN, d);
        }
        if self.they_laser {
            s += self.laser * table(&THEIR_LASER, d);
        }
        s
    }
    pub fn deal(&self, d: f64) -> f64 {
        let mut s = 0.0;
        if self.we_gun {
            s += self.gun * table(&OUR_GUN, d);
        }
        if self.we_laser {
            s += self.laser * table(&OUR_LASER, d);
        }
        s
    }
    /// Lower is better: what the exchange costs us per point of damage dealt.
    pub fn score(&self, d: f64) -> f64 {
        (self.take(d) + 30.0) / self.deal(d).max(1.0)
    }
    /// Best distance from 30 m to `cap` in 10 m steps; `base` unless the best is at least 10% better.
    pub fn best(&self, base: f64, cap: f64) -> f64 {
        let s0 = self.score(base);
        let mut best = (s0, base);
        let mut d = 30.0;
        while d <= cap + 1e-6 {
            let s = self.score(d);
            if s < best.0 {
                best = (s, d);
            }
            d += 10.0;
        }
        if best.0 <= s0 * 0.9 {
            best.1
        } else {
            base
        }
    }
}

impl Brain {
    pub(crate) fn so_on(&self) -> bool {
        self.p.so_on > 0.5
    }

    /// Sustained damage rates from the match config: a full magazine over its firing and reload time, and one
    /// full laser charge over charge plus cooldown.
    pub(crate) fn exchange(&self, obs: &Obs, tr: &Track) -> Exchange {
        let w = &self.cfg.weapons;
        let g = &w.gun;
        let gun = g.damage * g.mag / (g.mag * g.cooldown + g.reload).max(0.1);
        let laser = w.laser.damage / (w.laser.charge + w.laser.cooldown).max(0.1);
        let has = |k: WeaponKind| obs.me.weapons.iter().flatten().any(|x| x.kind == k);
        Exchange { gun, laser, we_gun: has(WeaponKind::Gun), we_laser: has(WeaponKind::Laser), they_gun: tr.has(WeaponKind::Gun) || tr.weapons.is_empty(), they_laser: tr.has(WeaponKind::Laser) }
    }

    /// Stand-off distance for a fight with `tr`: only with a laser in hand; a gun alone keeps `base`.
    pub(crate) fn so_range(&self, obs: &Obs, tr: &Track, base: f64) -> f64 {
        if !self.so_on() {
            return base;
        }
        let ex = self.exchange(obs, tr);
        if !ex.we_laser {
            return base;
        }
        ex.best(base, self.p.so_cap.max(30.0))
    }
}
