use super::math::*;
use super::Brain;
use crate::proto::{ActorKind, Obs, WeaponKind};

/// Cost of ending a step at `end` inside the zap radius `r` of a lightning carrier at `c`:
/// zero outside, rising steeply towards the carrier.
pub(crate) fn zap_cost(carriers: &[V3], end: V3, r: f64, w: f64) -> f64 {
    let mut s = 0.0;
    for c in carriers {
        let d = dist2(*c, end);
        if d < r {
            s += w * ((r - d) / r) * (1.0 + (r - d) / 20.0);
        }
    }
    s
}

impl Brain {
    /// The anti-lightning field applies unless we carry lightning with energy to spare and are healthy.
    pub(crate) fn zap_on(&self, obs: &Obs) -> bool {
        let p = &self.p;
        if p.zap_avoid < 0.5 {
            return false;
        }
        let me = &obs.me;
        let armed = me.weapons.iter().flatten().any(|w| w.kind == WeaponKind::Lightning) && me.energy >= p.zap_energy;
        !(armed && frac(me.hp, me.max_hp) >= p.zap_min_hp)
    }

    /// Positions of every enemy mech seen lately that carries lightning.
    pub(crate) fn zap_carriers(&self, obs: &Obs) -> Vec<V3> {
        if !self.zap_on(obs) {
            return Vec::new();
        }
        let t = obs.t;
        self.mem.tracks.iter().filter(|tr| tr.kind == ActorKind::Mech && t - tr.last_seen < self.p.zap_seen_s && tr.has(WeaponKind::Lightning)).map(|tr| tr.pos_at(t)).collect()
    }

    /// False for items that sit inside the zap radius of a lightning carrier.
    pub(crate) fn zap_item_ok(&self, obs: &Obs, q: V3) -> bool {
        if !self.zap_on(obs) {
            return true;
        }
        let r = self.p.zap_r;
        self.zap_carriers(obs).iter().all(|c| dist2(*c, q) >= r)
    }
}
