# Ice arrows melting red ice

Investigation: 2026-11-22, solo. Sources: the local base game + update
v131072 NUP, resolved with `Nup.latest()`. Implemented afterward in
`src/oot_rando/patching/blue_fire_arrows.py`, with resource and logic regression
tests in `tests/test_blue_fire_arrows.py`. Engine validation remains pending.

## Findings from native resources

Blue flame does not melt red ice through an elemental damage entry. The shared
red-ice AI receives a blue-fire message and enters its native melt sequence.
Adding a blue-fire damage element to an arrow is therefore not sufficient on
the evidence inspected.

Primary resource: `Pack/Actor/Obj_RedIce_A_01.pack.zs`, embedded
`AI/Obj_RedIce_A_01.root.ainb`:

- Node 51, `QueryMessageBlueFireReceiveMessage`, receives message `890699779`.
- Nodes 28 and 45 gate receipt and target matching. Node 31 enters the accepted
  flame path, including placing the representative bone at the flame position.
- Node 30 sets the local blackboard `IsFireHit` to true.
- Node 13 continuously exports that flag as the actor-logic output
  `isHitBlueFire`.
- The native path also disables respawning (node 42), saves the configured
  `MeltSaveGameDataName` when nonempty (nodes 4/2), holds LOD, plays
  `Melt_RedIce`, hides the model, and requests death. These effects must be
  preserved rather than replacing melting with immediate deletion.
- `GameParameter/RedIce/Obj_RedIce_A_01.game__object__RedIce.bgyml` specifies
  `MeltFrame: 200.0`.

The seven packs `Obj_RedIce_A_{01,02,04,06,07,08,09}` contain **byte-identical**
copies of that graph: 20,507 bytes, SHA-256 prefix `1d7330296798`.
All seven copies must be covered. The actor already has a `SensorHitRef` and an
object target sensor (`Obj_RedIce_A_01_Tg`); actual ice-arrow hit registration
on that sensor still needs an engine test.

`Obj_RedIce_A_03` is different: a passive static object with no melt AI or
SensorHit component. Do not automatically convert it into meltable ice.
Scanning the effective Banc maps found 17 direct placements of the seven
meltable variants and one of the passive variant; prefab-created instances
are outside that direct-placement count.

Primary arrow resources:

- `Pack/Actor/Pouch_Range_BowAndArrow_Ice.pack.zs`, embedded
  `GameParameter/AssginableItemParam/Item_Arrow_Ice.game__actor__AssginableItemParam.bgyml`,
  identifies `IceArrow` and selects `NormalArrow` as the shootable actor.
- `Pack/Actor/NormalArrow.pack.zs`, embedded
  `GameBalance/AttackParam/NormalArrow.game__gamebalance__AttackParam.bgyml`,
  contains separate normal/fire/ice/light attack rows. The ice row's
  `AttackType` is `3740198074`, confirmed as `murmur3('IceArrow')` using the
  existing project hash function. Its `AttackIdentifier` is currently empty.
- Ice already has its own damage element and multiplier. Preserve them to
  retain its existing combat behavior.

An existing native AI node can recognize a custom attack identifier:
`QuerySensorHitIsHitAttackIdentifier`. Its String input is `AttackIdentifier`,
and its Bool output is `Bool`. A concrete serialized template is in
`Pack/AI.Global.Product.111.pack.zs`, embedded
`AI/_LocalModule_7498b5490eff.module.ainb`, node 1. Its interface is also
declared in `AI/NodeDefinition/Node.Product.111.aidefn.byml.zs`.

## Implementation

1. Give only the ice attack row in `NormalArrow` a private identifier such as
   `Rando_BlueFireIceArrow`. Recompute its `AttackIdentifierHash` using murmur3;
   preserve its attack type, damage elements, damage multiplier, and all other
   rows. Runtime propagation of this identifier by the bow remains to be tested.
2. In each of the seven red-ice packs, add an identifier-hit query, OR its
   result with the native `IsFireHit` latch, and allow that signal through the
   native receipt/target gates. The accepted path still sets `IsFireHit` and
   uses the existing melt/persistence/respawn behavior. Keep the original
   blue-flame message path intact.
3. Do not route an arrow through nodes that require a received flame message's
   position or sender. In particular, node 27 consumes the message position;
   the implementation skips that node for the arrow/latch path and retains the
   ice's representative position. Do not supply an absent message.
4. Ensure the new branch observes hits every frame while ice is intact, and
   latches once. Preserve `isDelayMelt`, `isHitBlueFire`, and `Dead` outputs.
   The latter two drive existing map/prefab logic, including melt completion.
5. Integrate through the existing resource composition and RESTBL allocation
   path. Gate both the resource patch and solver behavior on the same option.

This approach uses existing engine nodes and resource edits; it is a candidate
that does not initially require executable hooks. Static inspection cannot
prove that the runtime retains the custom attack identifier or exposes the
hit through this sensor. If either fails, investigate the native SensorHit /
shootable code before choosing a fallback.

## Logic integration

The repository already has the helper in
`src/oot_rando/logic/world/helpers.json`:

```text
Blue_Fire = 'Blue Fire' or Buy_Blue_Fire or
            (blue_fire_arrows and can_use(Ice_Arrows))
```

`src/oot_rando/logic/rules.py` defaults `blue_fire_arrows` to false. It is now
exposed as an experimental Items option by `rando_options.py`, including AP
YAML export/import and generated AP option definitions. A false value is
omitted from recorded settings for compatibility with older artifacts. Seed
generation applies the eight pack patches only when enabled; independent
verification rebuilds the expected packs from the NUP and checks both enabled
and disabled behavior. Tests check the bow, arrows, magic, and adult requirements.

## Verification before enabling

- Offline: exact diff of the single ice attack row; all seven graph copies;
  serialize/parse round trips; idempotence; original message path preserved;
  shared patches compose; RESTBL sizes valid; solver only accepts usable ice
  arrows when the setting is enabled.
- Engine: ice arrow melts a red-ice wall and ice around a chest; normal, fire,
  and light arrows do not; bottled blue fire still works; ordinary enemy
  freezing/damage remains unchanged; repeated hits do not duplicate effects.
- Engine: save/reload and leave/reenter preserve native destruction behavior;
  dependent doors/checks activate on native melt completion.
- Engine: King Zora's linked ice, thaw sequence, and shuffled gift still work.
  His pack references the ice through `QueryActorGetReferenceLinkDstActor`
  (`AI/_LocalModule_5c35034f5308.module.ainb`), so preserving the native actor
  lifecycle matters. This interaction has not been proven by this investigation.

Local inspection artifacts are under ignored `build/blue-fire-research/`.
Validation seeds are generated only under `build/blue-fire-research/`.
No installed mod or save was changed.
