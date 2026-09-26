---
name: wh40k-deployment-planner
description: Plan where to set up Warhammer 40K (11th edition) units during deployment, drop by drop, using wargame game theory — cover, Hidden, objectives, shooting lanes, charge staging, screening and countering the opponent — on a Tabletop Simulator board.
---

# Warhammer 40K Deployment Planner (11th edition, Chapter Approved 2026-27)

Deployment is the only phase of 40K where both players move with (almost) perfect
information and nobody has rolled a die yet. It is a small sequential game followed by a
coin flip (the first-turn roll-off). This skill turns that structure into a repeatable
procedure: build a model of the board, score candidate positions, and recommend each drop
while reacting to what the opponent has placed.

Use it for pre-game planning ("here is the mission and both lists, plan my deployment") and
live, drop-by-drop play on Tabletop Simulator (TTS) ("they just put Eradicators down —
where does my next unit go?"). In this repo, read the table with `board.py` (§2).

---

## 1. Intake — get these before recommending anything

Ask for whatever is missing, in one message. Don't guess the deployment map or terrain; a
wrong map makes every recommendation wrong.

1. **Mission**: both Force Dispositions and both Primary Missions, the Deployment card
   (Tipping Point, Sweeping Engagement, Search and Destroy, Hammer and Anvil, Dawn of War,
   Crucible of Battle) or the GW-app recommended layout (A/B/C), any Twist, and whether each
   player uses Fixed (which two?) or Tactical secondaries.
2. **Roles**: who is Attacker and who is Defender (if not yet chosen, advise on the choice — §4.1).
3. **Terrain**: every terrain area with position, footprint size, category (dense / light /
   exposed), notable heights (any floor above 3"), and which areas are objectives and of
   which type (home / expansion / central, one or two centrals).
4. **Both army lists**: per unit — keywords (INFANTRY, VEHICLE, MONSTER, FLY, TITANIC…), M,
   OC, model count, key weapon ranges and whether they're [ASSAULT]/[HEAVY]/[INDIRECT FIRE],
   plus deployment-relevant abilities (Infiltrators, Scouts X", Deep Strike, Stealth,
   detection-range modifiers, ingress denial auras, Lone Operative, redeploy rules).
5. **Battle formations** (revealed before deployment): which units are in Strategic Reserves
   and which are embarked in which transports — for both players.
6. **Current state** in live play: what has been set up so far, by whom, and where.

Read what you can from the table yourself (§2) before asking for it: `board.py summary`
gives the units and terrain; the mission, lists and battle formations still come from the user.

---

## 2. Working with the table through tts-bridge

This repo talks to the running TTS game through the External Editor API, so read the
board and move models with its tools instead of asking the user to describe positions.
Only one process can hold the bridge's listener port: if the web app (`app/server.py`) or
`tts_bridge.py listen` is running, ask the user to stop it first.

**Coordinates.** Table inches, 0,0 at the centre. x runs along the 60" edge (−30…30), z along
the 44" edge (−22…22), y is height. In this repo Red is the first army, faces −z and has its
reserves board on the +z side, so Red normally deploys at +z and Blue at −z — confirm it
against the deployment zones in the summary. Facing is degrees: 0 = +z, 90 = +x, 180 = −z, 270 = −x. Ask which colour the
user is playing, then give every position both as `(x, z)` + facing and in plain words from
the user's side ("18" in from your left edge, 4" from your back edge, behind the L-ruin").

**Reading the board.**

```bash
python3 board.py summary        # units per army with centre, facing, model count and the
                                # terrain / zones they touch; terrain boxes and heights.
                                # Writes the full detail (every guid, bounding boxes) to board.json
python3 board.py dist "Pathfinder" "Intercessor" --nth-b 2   # closest base-to-base distance
python3 tts_bridge.py run "<lua>"   # anything else, e.g. a specific object's bounds
```

- Units are found by the `[<unit name>]` line that `army.py` and `recreate.py` write into each
  model's description; the army comes from GM Notes (`army.py:<list title>`, or
  `recreate:<scene>:Red|Blue`). Models placed some other way show up as untagged terrain or
  not at all — ask the user which objects are which if the summary looks thin.
- Two units with the same name are told apart by position (`#1` is the one nearest the +z
  edge). Units off the table (reserves boards) are listed separately.
- Terrain in the summary is every locked object on the table that isn't a model, with its
  footprint and top height; flat pieces (top under ~0.5") are usually LCT's terrain-area mats,
  taller ones are features. LCT's scripting zones (deployment zones, objectives) are listed as
  `zone` by name, but only as bounding boxes: for diagonal or cut-out zones (Crucible of Battle,
  Search and Destroy) work from the deployment card's geometry instead.
- `summary` gives footprints, not shapes, walls, windows or floors. For line-of-sight calls
  that hinge on a specific wall, ask the user to check in TTS or send a screenshot.
- `deploy_demo.py` shows a hand-written plan for the Tau list checked against a Search and
  Destroy zone and terrain boxes — a useful pattern for scripting a whole deployment.

**Placing a unit** (only the user's own units, and only when the user asks you to move them —
in a game against a person, recommend and let them place unless told otherwise):

```bash
python3 board.py place "Pathfinder Team" 6 17 180 --cols 5 --check   # validate only
python3 board.py place "Pathfinder Team" 6 17 180 --cols 5           # move it
python3 board.py undo                                                  # put it back
```

`place` lays the unit out in rows of `--cols` models (front row toward the facing) centred
on x, z, and refuses to move it if a base would leave the table, overlap another model, or
end within 2" of an enemy; `--force` overrides. It prints the nearest enemy distance and
the terrain and zones the unit would touch. It does **not** know deployment-zone shapes or
unit keywords: check "wholly within your deployment zone", vehicles against dense terrain,
and upper floors (use `--y` for the drop height) yourself. Use `--from-reserves` to bring a
unit in from the reserves board, `--army` / `--nth` to pick between same-named units.

**Measuring.** Distances are base to base with bases treated as circles, so treat anything
within 1" of a threshold (8" ingress, 2" engagement, 15"/12" detection, a weapon's range) as
uncertain: tell the user to confirm it with the TTS ruler, and leave a 0.5" margin.

---

## 3. The game theory, translated into deployment rules

Each idea below is paired with the decision rule it produces. Use them as lenses; the
scoring in §6 is where they become numbers.

### 3.1 Deployment is a sequential game with a coin flip after it
Chapter Approved sequence: Declare Battle Formations (reserves and transports, revealed) →
Deploy Armies (alternating one unit at a time, **Defender first**) → Redeploy (Attacker
resolves first) → **Determine First Turn (plain roll-off)** → Pre-battle rules such as Scouts
(first-turn player resolves first).

- Everything you place is seen before the opponent's next choice: perfect information
  inside deployment, so the later mover has an information advantage (a Stackelberg
  follower sees the leader's commitment).
- **Who goes first is decided after deployment, 50/50.** Your deployment must be good in
  *both* worlds. Evaluate every position twice — "I go first" and "they go first" — and prefer
  the one with the best worst case (maximin), unless you are clearly behind on paper and
  need variance (then take the higher-upside, higher-risk line on purpose and say so).

### 3.2 Drop order: commit invariant pieces early, hold reactive pieces late
A placement has *commitment value* (it shapes the opponent's options) and costs you
*option value* (you lose the chance to react). So:

- **Early drops**: units whose best spot barely depends on the opponent — home-objective
  holders, infantry going into obvious Hidden positions, screens along the ingress edges,
  backfield action-monkeys.
- **Late drops**: counter-punch melee, anti-tank, and your biggest shooting piece — the units
  whose best spot depends most on where the enemy's threats sit.
- **Drop count matters.** When one player finishes, the other places all remaining units
  with full information. Count both sides' drops (attached units and a transport with its
  cargo are one drop each; a TITANIC unit also skips its owner's next drop). With fewer drops,
  expect the opponent's last several units to be placed as counters; lean on Strategic
  Reserves and Scouts-from-reserve to hide information instead.
- **Scouts held in Strategic Reserves** may set up anywhere wholly within your deployment
  zone at the start of the battle — after deployment *and* after the first-turn roll. That is the most
  informed placement in the game; use it for the unit whose position is most matchup-dependent.

### 3.3 Minimax threat mapping (assume they go first)
For every enemy unit, compute its turn-1 reach and treat the union as the danger map:

- Shooting reach = M (+ D6 advance only for [ASSAULT] weapons; use 3.5 average, 6 worst
  case) + weapon range, limited by line of sight, Obscuring and Hidden.
- Charge reach = M + charge roll; a charge target must be within the rolled distance (the
  roll is made before choosing targets), and units that advanced can't charge. A unit
  arriving by ingress is set up more than 8" away, so needs 9+ (≈28%).
- 2D6 ≥ N: 5+ 83%, 6+ 72%, 7+ 58%, 8+ 42%, 9+ 28%, 10+ 17%, 11+ 8%, 12 3%.
- Minimise the worst case: position so that the enemy's most dangerous single turn kills as
  little as possible, *then* maximise your own damage within that constraint.

### 3.4 Colonel Blotto: allocate across lanes and objectives, don't spread evenly
Divide the table into 2–4 lanes (usually left / centre / right, following terrain). Each lane
has a value (the VP it feeds, from both Primaries and likely secondaries) and a cost to hold.

- In Blotto games, splitting evenly loses to any concentrated opponent. If you are weaker
  overall, concentrate on the lanes you can win outright and **concede one on purpose**
  (refused flank / oblique order), holding it only with cheap screens or scoring units.
- If you are stronger or faster, spread enough to threaten every lane so the opponent has to
  guess.
- As the opponent drops units, update: their overload on one lane makes your investment
  elsewhere cheaper. Match their overload only if that lane holds VP you can't give up.

### 3.5 Lanchester: concentrate fire, avoid being fed in piecemeal
Ranged combat follows something like the square law: effectiveness scales with the square of
the number of guns that can bear at once. Melee is closer to the linear law (fights are local).

- Build **kill zones**: place shooting units so two or more of them cover the same landing
  spots, lanes and objectives. One lane covered by three units beats three lanes covered by one.
- Deploy so your army arrives together; units that are 1–2 turns apart get defeated in detail.
- The reverse: position so the opponent can only bring part of his army to bear on any one
  of your units on turn 1 (break their lines with Obscuring terrain).

### 3.6 Credible threats, denial and zones of control
- A deployed threat that the opponent must respect is **deterrence**: a melee unit 20" away
  from a lane closes that lane even if it never moves. Place threats where their bubble
  covers the opponent's natural staging terrain.
- **Ingress denial**: enemy reserves must arrive more than 8" from every one of your units.
  Two screens deny the gap between them only if their nearest models are no more than 16"
  apart (plan 14" to be safe), and a screen covers the table edge only within 8".
  Standard Strategic Reserves arrive wholly within 6" of an edge and can't enter your
  deployment zone before battle round 3, so the first-turn danger is **Deep Strike**, which
  can land anywhere more than 8" away. Screen against what the opponent actually declared.
- **Engagement** is 2" — any unit projects a 4"-wide no-go band that enemies can pass
  through but not stop in, which makes cheap chaff excellent at blocking access to terrain
  areas and sightline nodes.

### 3.7 Deception and keeping options open
- Prefer positions that support **two plans** (a "fork"): a unit that can hit either the
  centre or the flank objective forces the opponent to defend both.
- Don't reveal your main effort early. Place the pieces that would telegraph it (your
  hammer, your deep-strike screen gaps) as late as the drop order allows.
- Against a known opponent across several games, vary your setups; against a stranger in a
  single game, just avoid obvious tells.

### 3.8 Regret check
Before confirming a drop, ask: "If they place their best counter next, how much worse off am
I than if I'd put this unit somewhere else?" If the answer is "a lot", the position is
fragile; pick the one with lower regret.

---

## 4. 11th-edition rules that decide placement

Verify anything marked (check) against the user's own rules source; datasheets and
detachment rules override the core rules.

| Rule | What it means for deployment |
|---|---|
| Battlefield 44" × 60"; set up **wholly within** your deployment zone | Measure the deepest model, not the unit centre. |
| Defender deploys first; alternate one unit at a time | See §3.2. |
| Benefit of cover = **−1 BS** to the attack | INFANTRY/BEASTS/SWARM get it just by being within a terrain area; everything else only if not fully visible due to intervening terrain / an Obscuring area. Worked out **per attacking model** — one fully exposed model loses cover against that shooter. |
| **Hidden**: INFANTRY/BEASTS/SWARM within a terrain area containing a light or dense feature, unit didn't shoot this turn or last | Only visible to enemies within **15"** detection range. On the first turn every such unit counts as not having shot, so **infantry deployed in terrain start the game Hidden**. |
| **Gone to Ground**: Hidden and not fully visible due to intervening dense terrain | Detection range drops by 3" to **12"**. Put infantry behind a dense wall inside the area, facing the enemy. Using Overwatch switches Hidden off. |
| **Obscuring**: areas with a light or dense feature block LOS if every line crosses them | Doesn't count an area that either model is **within** (even partly). Big central areas become sightline nodes — whoever touches them sees across. Deny the enemy those nodes on turn 1; secure them for yourself. |
| **Solid** (dense): no LOS through enclosed gaps ≤ 3" from the ground | Ground-floor windows don't expose you. Upper floors can see and be seen. |
| **Plunging Fire**: shooter on a section > 3" high vs a target with models at ground level | +1 BS — cancels cover. A deployment spot on a > 3" floor is a strong firing position but gives up Hidden once it shoots. |
| MONSTER/VEHICLE (non-FLY) vs dense terrain | Can't pass through dense sections over 2" tall and can't end on upper levels. Check that each vehicle has an exit lane; one blocked turn is a lost turn. |
| FLY: **Take to the Skies** | −2" to max move, then ignores models and terrain. |
| Engagement range 2" horizontal / 5" vertical; coherency 2" to one model and 9" to all | Units can't string out; screens cover less width than in 10th. |
| Ingress (reserves arriving) | Must be > 8" from all enemy units. Strategic Reserves: wholly within 6" of an edge, not in the enemy DZ before round 3, not before round 2 unless a rule says so, **destroyed if not arrived by end of round 3**. Deep Strike: anywhere > 8" from enemies. Reserves cap is half the army's points, all kinds combined. A unit that ingressed can't move again until the next Charge phase. |
| Infiltrators (check) | Set up outside your deployment zone using the 8" distance rules. Can't also make a Scout move. |
| Scouts (check) | Pre-battle move made by the first-turn player first; no longer usable if deployed outside your DZ. If held in Strategic Reserves, can instead set up anywhere wholly within your DZ at the start of the battle. |
| Heavy weapons | +1 to hit if the unit moved no more than 3" — deploy heavy shooters where they already see their lanes. |
| Fire Overwatch | End of the enemy's Movement phase; one unit; snap shooting (hits only on 6s, within 24"). Position a cheap overwatch unit covering the lanes the enemy must cross. |
| Heroic Intervention | Can counter-charge anything within 6" for extra CP — counter-charge units should sit about 6" behind the screens they protect. |
| Aircraft | Never deployed on the table; they ingress within 6" of any edge each turn. |
| Night Fighting twist | Nothing visible beyond 18" — drastically shrinks turn-1 danger maps. **Nowhere to Hide** removes Solid (more LOS). |

**Objectives are terrain areas.** A model is in range of a terrain objective when it is
within the area; control is total OC in the area. Types: one **home** per deployment zone,
two **expansion** objectives in no-man's land (one closer to each player), and one or two
**central** objectives. Objectives in terrain mean holders are usually Hidden too.

**Mission timing.** Most Primaries score from battle round 2 at the end of the Command
phase — so what matters is who stands in each area at the *start of your round-2 turn*,
after the opponent's turn 1. Exceptions reward turn-1 grabs: Battlefield Dominance (rounds
1–2, control more), Outmanoeuvre (round 1, 4 VP per objective), Gather Intel (round 1,
central = 6 VP), Immovable Object (central, any round). Read both Primaries: the opponent's
card tells you what he is going to try to take from you. Secondaries that change deployment:
Behind Enemy Lines, Engage on All Fronts / Outflank (table quarters and edges), Display of
Might (units wholly in no-man's land), Centre Ground (within 3" of centre), Beacon, Defend
Stronghold, Plunder, A Tempting Target, Secure No Man's Land.

### 4.1 Attacker/Defender choice (winner of the roll-off decides)
Choose by board first, information second:
1. If one side's deployment zone has clearly better terrain for your army (Hidden spots for
   your infantry, Obscuring cover for your vehicles, sightline nodes you can reach), take it.
2. Otherwise take **Attacker**: you deploy second, so you drop into a board that shows more of
   the enemy's plan. (The Attacker resolves redeploys first, a minor cost.)
3. Exception: if you have far fewer drops, the tail of deployment belongs to the opponent
   either way, and the side matters more than the order.

---

## 5. Procedure

Work through these steps and show the user the short outputs, not the arithmetic.

**Step 1 — Map the board.** List terrain areas with coordinates, category, height, objective
type. Then identify:
- **Lanes**: open corridors between Obscuring areas where fire and movement flow.
- **Sightline nodes**: areas that, when touched, open long lines of sight (usually the big
  central ruins). Note who can reach each on turn 1.
- **Dead ground**: spots in each deployment zone that no enemy deployment position can see
  (behind Obscuring areas from every point of the enemy DZ). Vehicles and monsters go here.
- **Hidden seats**: terrain areas in your DZ (and reachable ones) where infantry can be
  Hidden and ideally Gone to Ground, plus the distance from the nearest enemy DZ edge.

**Step 2 — Value map.** For each objective and lane, write the VP it feeds for **each**
player per round (Primary + likely secondaries). Mark "must hold", "contest", "deny", "ignore".

**Step 3 — Threat model.** Build the enemy's turn-1 danger map (§3.3) and his deep-strike
and reserve plan (known from Battle Formations). Also build your own reach map — what you
threaten if you go first.

**Step 4 — Role assignment (Blotto).** Give every unit one job: *anchor* (holds home, sits
Hidden), *screen* (denies ingress or blocks lanes), *fire base* (covers kill zones),
*hammer* (strikes turn 1–2), *flex/counter-punch* (responds to what comes), *scorer* (actions,
secondaries), *reserve*. Choose the lane(s) you contest and the one you concede, and state
the plan in one sentence ("Hold home and left expansion, deny the centre, kill whatever
steps onto it").

**Step 5 — Candidate positions.** For each unit, generate 2–3 candidate spots and score them
(§6) in both first-turn worlds.

**Step 6 — Drop order.** Sort units by how much their best position depends on the enemy
(invariant first, reactive last, §3.2), adjusted so screens go down before the units they
protect only if the opponent would otherwise exploit the gap.

**Step 7 — Live loop (one drop at a time).** After each enemy drop:
1. Update the danger map and lane allocations with the new unit.
2. Name the counter it demands, if any (does it threaten a unit already placed? a lane?).
3. Recommend the next drop with the output template (§8). Re-score only what changed.

**Step 8 — Redeploy and pre-battle.** After both armies are down: resolve redeploy abilities
(Attacker first). After the first-turn roll: if you go first, Scout toward turn-1 objectives
and firing positions; if you go second, Scout only into Hidden spots or leave units where
they are. Place Scouts-from-reserve last with everything known.

**Step 9 — Final checklist** before the first turn:
- [ ] Every INFANTRY unit that isn't meant to shoot turn 1 is inside a terrain area (Hidden),
      ideally behind dense walls (12" detection).
- [ ] Every VEHICLE/MONSTER is in dead ground or behind an Obscuring area the enemy can't
      step into on turn 1.
- [ ] No unit has a single model hanging out of cover in view of enemy guns.
- [ ] Screens leave no landing spot > 8" from all of your units inside the area you meant to
      deny (gaps ≤ 14–16", table edges within 8").
- [ ] Each vehicle has a clear exit lane (no dense terrain over 2" in the way).
- [ ] Home objective has an OC holder; round-1 scoring objectives have a turn-1 runner.
- [ ] Charge units sit so their M + 7" reaches the enemy's likely staging spots on turn 2.
- [ ] You have checked both worlds: what dies if they go first, what you kill if you go first.

---

## 6. Scoring a candidate position

Score each factor 0–5, apply weights, and compute the score twice — **S₁** (you go first)
and **S₂** (they go first).

| Factor | Question |
|---|---|
| **Survival (Sv)** | If they go first, how much of this unit dies? Count enemy units that can see it (after Hidden, Obscuring, Gone to Ground) and reach it by charge or deep strike; 5 = untouchable, 0 = deleted. |
| **Objective (Ob)** | How much mission VP does this position enable by the start of round 2 — holding, contesting or denying? Weight by the value map. |
| **Offense (Of)** | If you go first, how many worthwhile targets does it hit or charge? Include Plunging Fire and kill-zone overlap. |
| **Denial (De)** | Does it deny ingress, block a lane, or deny a sightline node? |
| **Synergy (Sy)** | Aura and leader range, overlapping fire (Lanchester), support for a nearby unit. |
| **Flexibility (Fx)** | How many distinct useful plans does it support next turn (the fork)? |
| **Concealment (Co)** | How little does placing it here reveal about your plan (high for "obvious" spots, low for tells)? |

Default weights (adjust by archetype, §7): Sv 3, Ob 3, Of 2, De 2, Sy 1, Fx 1, Co 1.

`S₁` uses all factors normally. `S₂` doubles the weight of Survival and halves Offense (you
will be reacting). Choose the candidate with the best **min(S₁, S₂)**; break ties with the
average. If you are the underdog and need variance, say so and use the average instead of
the minimum.

Show the user only the winner, the runner-up, and the one-line reason the winner won.

---

## 7. Archetype adjustments

- **Gunline**: Sv 3, Of 3, De 2 (screen deep strike hard). Sit back, own the sightline nodes,
  use Plunging Fire spots, deny forward infantry the terrain that would let them see you.
- **Melee rush**: Of 3, Fx 2, Sv 3. Deploy fast units in dead ground on the line of your main
  effort, as close to the front edge as possible, each within M + 7" of an enemy staging
  area. Keep units Hidden until they move; put cheap units in front to soak Overwatch and tie up the enemy's screens.
- **Balanced / midrange**: default weights. Win the centre on turn 2, not turn 1.
- **Horde / cheap bodies**: De 3, Ob 3. Use chaff to screen, block access to sightline nodes
  and contest objectives; spend drops early to watch the enemy's tail commit.
- **Elite / few drops**: Sv 4, Co 2. Expect to be counter-deployed; use reserves and
  Scouts-from-reserve to keep hidden information; prefer positions that are good against
  any counter.
- **Reserve-heavy**: plan landing zones you'll use on round 2 (and the enemy screens
  denying them), and deploy on-table units to break up those screens on turn 1.

**Against the opponent's archetype**: versus deep strike, screen; versus a gunline, hide
everything and don't give free lines; versus melee, keep 12" or more of dead space and a
counter-charge unit ~6" behind the screen.

---

## 8. Output template (each recommendation)

```
NEXT DROP: <Unit name>  (role: <anchor/screen/fire base/hammer/flex/scorer>)
WHERE: (x, z) = (…, …), facing …°   → board.py place "<unit>" x z facing --cols N
       …" from your left edge, …" from your back edge — <landmark>
FORMATION: <e.g. 3 models inside the ruin behind the north wall, 2 on the east side; nothing past the area boundary>
WHY: <1–3 short reasons tied to cover/Hidden, objectives, lanes, charge staging, denial>
IF THEY GO FIRST: <what can reach it and expected loss>
IF YOU GO FIRST: <what it can do turn 1>
WATCH FOR: <the enemy drop that would change this and how you'd respond>
CONFIRM WITH RULER: <any distance within 1" of a threshold>
```

For a full pre-game plan, start with the one-sentence plan and lane allocation, then list the
drop order with a one-line position per unit, then the pre-battle (Scouts / redeploy)
branches for "we go first" and "they go first".

---

## 9. Common mistakes to catch

- Planning only for going first. The roll happens *after* deployment.
- Vehicles "hidden" behind a ruin the enemy can step into on turn 1 (Obscuring stops working
  for a shooter within the area).
- One infantry model poking out of the terrain area — no cover against that shooter, and not
  Hidden.
- Screens spaced more than 16" apart, or leaving a corridor along the table edge.
- Placing the hammer early and letting the opponent deploy away from it.
- Units in reserve that can't find a legal landing spot by the end of round 3 (they are destroyed).
- Ignoring the opponent's Primary: it tells you where his army is going.
