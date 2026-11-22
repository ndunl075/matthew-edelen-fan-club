use super::math::*;
use super::memory::Track;
use super::{Brain, Mode, Tgt};
use crate::proto::{ActorKind, Obs};

impl Brain {
    /// True when a mech seen in the last few seconds is within `r` of `q`.
    pub(crate) fn near_foe(&self, obs: &Obs, q: V3, r: f64) -> bool {
        let t = obs.t;
        self.mem.tracks.iter().any(|tr| tr.kind == ActorKind::Mech && t - tr.last_seen < 4.0 && dist2(tr.pos_at(t), q) < r)
    }

    /// Opening window: the first `open_s` seconds of the match.
    pub(crate) fn opening(&self, t: f64) -> bool {
        self.p.open_s > 0.0 && t < self.p.open_s
    }

    /// Items next to a known mech are skipped while the opening window is on.
    pub(crate) fn open_item_ok(&self, obs: &Obs, q: V3) -> bool {
        !(self.opening(obs.t) && self.p.open_avoid_r > 0.0 && self.near_foe(obs, q, self.p.open_avoid_r))
    }

    /// Positions of mechs worth keeping away from (seen recently, not clearly weak) while calm looting.
    pub(crate) fn keep_foes(&self, obs: &Obs) -> Vec<V3> {
        let p = &self.p;
        let t = obs.t;
        if p.keep_w <= 0.0 || t >= p.keep_s || !matches!(self.mode, Mode::Idle | Mode::Loot | Mode::Hunt | Mode::Drop) {
            return Vec::new();
        }
        let me = &obs.me;
        let strong = frac(me.hp, me.max_hp) > 0.6;
        let mut v = Vec::new();
        for tr in &self.mem.tracks {
            if tr.kind != ActorKind::Mech || t - tr.last_seen > 2.5 {
                continue;
            }
            if strong && frac(tr.hp, tr.max_hp) < p.keep_weak_hp {
                continue;
            }
            let q = tr.pos_at(t);
            if dist2(q, me.pos) < p.keep_d {
                v.push(q);
            }
        }
        v
    }

    /// Score for ending a step at `end`: positive when it opens the gap to each near mech.
    pub(crate) fn keep_term(&self, foes: &[V3], pos: V3, end: V3) -> f64 {
        let mut s = 0.0;
        for q in foes {
            let d0 = dist2(*q, pos).min(self.p.keep_d);
            let d1 = dist2(*q, end).min(self.p.keep_d);
            s += self.p.keep_w * (d1 - d0);
        }
        s
    }

    /// Opening a fight with a mech that has not hit us: only at high health, one on one,
    /// or against a clearly weak mech. Fights already under way and attackers pass.
    pub(crate) fn fight_open_ok(&self, obs: &Obs, tr: &Track, attacker: bool) -> bool {
        let p = &self.p;
        if p.fg_on < 0.5 || attacker || self.target == Tgt::Mech(tr.id) {
            return true;
        }
        let t = obs.t;
        let me = &obs.me;
        let hpf = frac(me.hp, me.max_hp);
        let weak = frac(tr.hp, tr.max_hp) < p.fg_weak;
        if hpf < p.fg_hp && !(weak && hpf > 0.5) {
            return false;
        }
        if weak {
            return true;
        }
        let tp = tr.pos_at(t);
        !self.mem.tracks.iter().any(|o| o.kind == ActorKind::Mech && o.id != tr.id && t - o.last_seen < 4.0 && {
            let q = o.pos_at(t);
            dist2(q, me.pos) < p.fg_crowd || dist2(q, tp) < p.fg_crowd
        })
    }

    /// A third mech close by while a fight is on and health is not full: break contact.
    pub(crate) fn fight_third(&self, obs: &Obs, tgt: Option<&Track>) -> bool {
        let p = &self.p;
        let Some(tr) = tgt else { return false };
        if p.fg_on < 0.5 || frac(obs.me.hp, obs.me.max_hp) >= p.fg_third_hp {
            return false;
        }
        let t = obs.t;
        self.mem.tracks.iter().any(|o| o.kind == ActorKind::Mech && o.id != tr.id && t - o.last_seen < 2.0 && dist2(o.pos_at(t), obs.me.pos) < p.fg_third_r)
    }
}
