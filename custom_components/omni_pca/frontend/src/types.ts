// TS mirrors of the Phase-B websocket wire shapes. Short field names
// match websocket.py's _tokens_to_json — keep these in sync if the
// Python side changes.

export interface Token {
  /** "keyword" / "operator" / "ref" / "value" / "text" / "indent" / "newline" */
  k: string;
  /** Display text for this token. Empty for newline. */
  t: string;
  /** Object kind for REF tokens (zone / unit / area / thermostat / button / message / code / timeclock). */
  ek?: string;
  /** 1-based slot for REF tokens. */
  ei?: number;
  /** Live-state badge for REF tokens (e.g. "SECURE", "ON 60%"). */
  s?: string;
}

export interface ProgramRow {
  /** 1-based slot number. For chains, the head slot. */
  slot: number;
  /** "compact" or "chain". */
  kind: string;
  /** TIMED / EVENT / YEARLY / WHEN / AT / EVERY / REMARK / FREE. */
  trigger_type: string;
  /** One-line summary token stream. */
  summary: Token[];
  /** Flat ["unit:7", "zone:5", ...] for filter chips. */
  references: string[];
  condition_count: number;
  action_count: number;
}

export interface ProgramListResponse {
  programs: ProgramRow[];
  total: number;
  filtered_total: number;
  offset: number;
  limit: number;
  /** Edit / clone / clear of single-line programs, and undo. */
  can_write?: boolean;
  /** Editing multi-line WHEN / AT / EVERY blocks. */
  can_edit_chains?: boolean;
  /** The "Fire now" button. */
  can_fire?: boolean;
}

/** One journalled change, as ``omni_pca/programs/history`` returns it. */
export interface JournalEntry {
  id: string;
  time: string;
  action: string;
  status: "pending" | "applied" | "failed" | "undone";
  changes: Array<{ slot: number; before: string | null; after: string | null }>;
  undoes?: string;
  error?: string;
}

export interface ProgramDetail {
  slot: number;
  kind: string;
  trigger_type: string;
  /** Full structured-English token stream. */
  tokens: Token[];
  references: string[];
  /** For chain detail: every slot the chain spans. */
  chain_slots?: number[];
  /** Raw Program field values; included for compact-form programs so
   *  the editor can seed its form from real data rather than defaults. */
  fields?: ProgramFields;
  /** For chain detail: per-member role + raw fields. Drives the
   *  chain editor's row-per-slot rendering. */
  chain_members?: Array<{
    slot: number;
    role: "head" | "condition" | "action";
    fields: ProgramFields;
  }>;
}

export interface ProgramListRequest {
  type: "omni_pca/programs/list";
  entry_id: string;
  trigger_types?: string[];
  references_entity?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export interface ProgramGetRequest {
  type: "omni_pca/programs/get";
  entry_id: string;
  slot: number;
}

export interface ProgramFireRequest {
  type: "omni_pca/programs/fire";
  entry_id: string;
  slot: number;
}

// Raw Program dict — mirrors the dataclass on the Python side. Sent
// over the wire by ``omni_pca/programs/write``; the websocket validates
// each field's range and constructs the typed dataclass server-side.
export interface ProgramFields {
  prog_type: number;
  cond?: number;
  cond2?: number;
  cmd?: number;
  par?: number;
  pr2?: number;
  month?: number;
  day?: number;
  days?: number;
  hour?: number;
  minute?: number;
  remark_id?: number | null;
}

export interface ProgramWriteRequest {
  type: "omni_pca/programs/write";
  entry_id: string;
  slot: number;
  program: ProgramFields;
}

export interface NamedObject {
  index: number;
  name: string;
}

export interface ObjectListResponse {
  zones: NamedObject[];
  units: NamedObject[];
  areas: NamedObject[];
  thermostats: NamedObject[];
  buttons: NamedObject[];
}

// Command enum values we let the user pick from the editor. Mirrors the
// most useful subset of omni_pca.commands.Command. The second element
// is what object kind (if any) the command's pr2 parameter references —
// drives the object picker's filter.
/** Which extra inputs a command's action form needs. */
export type CommandForm =
  | "unit"         // pr2 = unit, par = how long (0 = until changed)
  | "level"        // pr2 = unit, par = level %
  | "timed-level"  // pr2 = level << 8 | unit, par = how long
  | "all"          // pr2 = area, 0 = every area
  | "zone"         // pr2 = zone
  | "button"       // pr2 = button
  | "area"         // pr2 = area
  | "link"         // pr2 = UPB link number
  | "setpoint";    // pr2 = thermostat (0 = all), par = raw temperature

export interface CommandOption {
  value: number;
  label: string;
  form: CommandForm;
  ref_kind: "unit" | "zone" | "area" | "button" | null;
}

export const COMMAND_OPTIONS: CommandOption[] = [
  { value: 0,   label: "Turn OFF unit",        form: "unit",        ref_kind: "unit" },
  { value: 1,   label: "Turn ON unit",         form: "unit",        ref_kind: "unit" },
  { value: 9,   label: "Set unit level %",     form: "level",       ref_kind: "unit" },
  { value: 101, label: "Set unit level % for a time", form: "timed-level", ref_kind: null },
  { value: 2,   label: "All OFF",              form: "all",         ref_kind: null },
  { value: 3,   label: "All ON",               form: "all",         ref_kind: null },
  { value: 4,   label: "Bypass zone",          form: "zone",        ref_kind: "zone" },
  { value: 5,   label: "Restore zone",         form: "zone",        ref_kind: "zone" },
  { value: 7,   label: "Execute button",       form: "button",      ref_kind: "button" },
  { value: 28,  label: "UPB link OFF",         form: "link",        ref_kind: null },
  { value: 29,  label: "UPB link ON",          form: "link",        ref_kind: null },
  { value: 66,  label: "Set heat setpoint",    form: "setpoint",    ref_kind: null },
  { value: 67,  label: "Set cool setpoint",    form: "setpoint",    ref_kind: null },
  { value: 48,  label: "Disarm area",          form: "area",        ref_kind: "area" },
  { value: 49,  label: "Arm area Day",         form: "area",        ref_kind: "area" },
  { value: 50,  label: "Arm area Night",       form: "area",        ref_kind: "area" },
  { value: 51,  label: "Arm area Away",        form: "area",        ref_kind: "area" },
  { value: 52,  label: "Arm area Vacation",    form: "area",        ref_kind: "area" },
];

export function commandOptionFor(value: number): CommandOption | undefined {
  return COMMAND_OPTIONS.find((c) => c.value === value);
}

// A unit command's duration byte: 1-99 = seconds, 101-199 = 1-99
// minutes, 201-218 = 1-18 hours, 0 = until changed. Anything else is
// kept as "raw" so the editor never rewrites a value it can't explain.
export type DurationUnit = "none" | "sec" | "min" | "hr" | "raw";

export interface DecodedDuration {
  unit: DurationUnit;
  value: number;
}

export function decodeDuration(par: number): DecodedDuration {
  if (par === 0) return { unit: "none", value: 0 };
  if (par >= 1 && par <= 99) return { unit: "sec", value: par };
  if (par >= 101 && par <= 199) return { unit: "min", value: par - 100 };
  if (par >= 201 && par <= 218) return { unit: "hr", value: par - 200 };
  return { unit: "raw", value: par };
}

export function encodeDuration(d: DecodedDuration): number {
  const clamp = (v: number, max: number) => Math.min(Math.max(Math.round(v) || 1, 1), max);
  switch (d.unit) {
    case "none": return 0;
    case "sec":  return clamp(d.value, 99);
    case "min":  return 100 + clamp(d.value, 99);
    case "hr":   return 200 + clamp(d.value, 18);
    case "raw":  return d.value & 0xFF;
  }
}

// Raw Omni temperature byte <-> whole degrees Fahrenheit, matching
// omni_pca.models.omni_temp_to_fahrenheit.
export function rawTempToF(raw: number): number {
  return Math.floor(raw * 0.9 + 0.5) - 40;
}

export function fToRawTemp(f: number): number {
  const guess = Math.round((f + 40) / 0.9);
  for (const raw of [guess, guess - 1, guess + 1]) {
    if (raw >= 0 && raw <= 255 && rawTempToF(raw) === f) return raw;
  }
  return Math.min(Math.max(guess, 0), 255);
}

// Days bitmask bits (matches omni_pca.programs.Days). Bit 0 is unused.
export const DAY_BITS: ReadonlyArray<{ bit: number; label: string }> = [
  { bit: 0x02, label: "Mon" },
  { bit: 0x04, label: "Tue" },
  { bit: 0x08, label: "Wed" },
  { bit: 0x10, label: "Thu" },
  { bit: 0x20, label: "Fri" },
  { bit: 0x40, label: "Sat" },
  { bit: 0x80, label: "Sun" },
];

// Program type constants (matches omni_pca.programs.ProgramType).
export const PROGRAM_TYPE_TIMED = 1;
export const PROGRAM_TYPE_EVENT = 2;
export const PROGRAM_TYPE_YEARLY = 3;
export const PROGRAM_TYPE_REMARK = 4;


// --------------------------------------------------------------------------
// Event-ID encode/decode for the EVENT-program editor.
//
// Mirrors the Python helpers in omni_pca.program_engine — the 16-bit
// event_id uses different bit patterns per category. Each "category"
// in the UI maps to a different chunk of the ID space.
// --------------------------------------------------------------------------


export type EventCategory =
  | "button"   // USER_MACRO_BUTTON   (evt & 0xFF00) == 0x0000
  | "zone"     // ZONE_STATE_CHANGE   (evt & 0xFC00) == 0x0400
  | "unit"     // UNIT_STATE_CHANGE   (evt & 0xFC00) == 0x0800
  | "allunits" // ALL ON / ALL OFF     (evt & 0xFFE0) == 0x03E0
  | "security" // security mode set    (evt & 0x8000) != 0
  | "fixed"    // hard-coded IDs (phone / AC power)
  | "raw";     // anything else — show numeric

export interface DecodedEvent {
  category: EventCategory;
  /** For "button": 1..255 */
  button?: number;
  /** For "zone": 1..511, plus state 0=secure / 1=not-ready */
  zone?: number;
  zoneState?: number;
  /** For "unit": 1..511 plus on bool */
  unit?: number;
  unitOn?: boolean;
  /** For "allunits": on/off, and the area (0 = every area). */
  allOn?: boolean;
  /** For "allunits" and "security": area number, 0 = any. */
  area?: number;
  /** For "security": mode 0..6 and user code (0 = any). */
  mode?: number;
  code?: number;
  /** For "fixed": the literal event ID. */
  fixedId?: number;
  /** For "raw": the literal event ID we couldn't classify. */
  raw?: number;
}

// Hand-rolled fixed IDs and labels (matches Python EVENT_* constants).
export const FIXED_EVENTS: ReadonlyArray<{ id: number; label: string }> = [
  { id: 768, label: "Phone line dead" },
  { id: 769, label: "Phone ringing" },
  { id: 770, label: "Phone off hook" },
  { id: 771, label: "Phone on hook" },
  { id: 772, label: "AC power lost" },
  { id: 773, label: "AC power restored" },
];

const ZONE_STATE_LABELS = ["secure", "not ready"];

export function decodeEventId(eventId: number): DecodedEvent {
  // FIXED first — the bit patterns below would otherwise collapse
  // 768..773 into the "zone state change" category since their top
  // bits look the same.
  if (FIXED_EVENTS.some((f) => f.id === eventId)) {
    return { category: "fixed", fixedId: eventId };
  }
  if ((eventId & 0xFF00) === 0x0000) {
    return { category: "button", button: eventId & 0xFF };
  }
  // Zone and unit events: bit 9 is the state, the low 9 bits the object
  // number (0x0605 = zone 5 not ready on a real panel).
  if ((eventId & 0xFC00) === 0x0400) {
    return {
      category: "zone",
      zone: eventId & 0x01FF,
      zoneState: (eventId & 0x0200) ? 1 : 0,
    };
  }
  if ((eventId & 0xFC00) === 0x0800) {
    return {
      category: "unit",
      unit: eventId & 0x01FF,
      unitOn: (eventId & 0x0200) !== 0,
    };
  }
  if ((eventId & 0xFFE0) === 0x03E0) {
    return {
      category: "allunits",
      allOn: (eventId & 0x0010) !== 0,
      area: eventId & 0x000F,
    };
  }
  if ((eventId & 0x8000) !== 0 && ((eventId >> 12) & 0x07) <= 6) {
    return {
      category: "security",
      mode: (eventId >> 12) & 0x07,
      area: (eventId >> 8) & 0x0F,
      code: eventId & 0xFF,
    };
  }
  return { category: "raw", raw: eventId };
}

export function encodeEventId(ev: DecodedEvent): number {
  switch (ev.category) {
    case "button":
      return (ev.button ?? 1) & 0xFF;
    case "zone": {
      const zone = (ev.zone ?? 1) & 0x01FF;
      return 0x0400 | (ev.zoneState ? 0x0200 : 0) | zone;
    }
    case "unit": {
      const unit = (ev.unit ?? 1) & 0x01FF;
      return 0x0800 | (ev.unitOn ? 0x0200 : 0) | unit;
    }
    case "allunits":
      return 0x03E0 | (ev.allOn ? 0x0010 : 0) | ((ev.area ?? 0) & 0x0F);
    case "security":
      return 0x8000 | (((ev.mode ?? 0) & 0x07) << 12)
        | (((ev.area ?? 0) & 0x0F) << 8) | ((ev.code ?? 0) & 0xFF);
    case "fixed":
      return ev.fixedId ?? 768;
    case "raw":
    default:
      return ev.raw ?? 0;
  }
}

export function eventIdFromFields(fields: ProgramFields): number {
  return ((fields.month ?? 0) << 8) | (fields.day ?? 0);
}

export function packEventIdIntoFields(
  fields: ProgramFields, eventId: number,
): ProgramFields {
  return {
    ...fields,
    month: (eventId >> 8) & 0xFF,
    day: eventId & 0xFF,
  };
}

export function zoneStateLabel(state: number): string {
  return ZONE_STATE_LABELS[state] ?? `state ${state}`;
}


// Month abbreviations for the YEARLY editor.
export const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];


// --------------------------------------------------------------------------
// Compact-form AND-IF condition encode/decode for the inline-conditions
// editor (TIMED/EVENT/YEARLY cond + cond2 fields).
//
// Mirrors clsText.GetConditionalText (clsText.cs:2224-2274) and the
// Python _emit_traditional_cond in program_renderer.py. Bit layout:
//
//   family = (cond >> 8) & 0xFC
//   selector bit = (cond & 0x0200) — meaning depends on family
//
//   family 0x00 OTHER  — cond & 0x0F = enuMiscConditional (NONE=0,
//                                     NEVER=1, LIGHT=2, DARK=3, ...)
//   family 0x04 ZONE   — low 8 bits = zone index; selector bit
//                        0=secure, 1=not ready
//   family 0x08 CTRL   — low 9 bits = unit index; selector bit
//                        0=OFF, 1=ON
//   family 0x0C TIME   — low 8 bits = time-clock index; selector bit
//                        0=disabled, 1=enabled
//   family >= 0x10 SEC — (cond >> 8) & 0x0F = area, (cond >> 12) & 0x07 = mode
//
// cond == 0 means "no condition" (NONE).
// --------------------------------------------------------------------------


export type CondFamily =
  | "none"   // cond = 0 — no inline condition
  | "misc"   // OTHER family (NEVER, LIGHT, DARK, PHONE_*, AC_POWER_*, …)
  | "zone"   // ZONE family — zone + secure/not-ready
  | "unit"   // CTRL family — unit + on/off
  | "time"   // TIME family — time-clock + enabled/disabled
  | "sec"    // SEC family — area + security mode
  | "raw";   // a value this editor can't rebuild exactly — kept as is

export interface DecodedCondition {
  family: CondFamily;
  /** misc-conditional index (0..15) — used when family == "misc". */
  misc?: number;
  /** Zone / unit / time-clock / area index — used by the named families. */
  index?: number;
  /** Selector bit: zone "not ready", unit "on", time-clock "enabled". */
  active?: boolean;
  /** SEC family security mode (0..7). */
  mode?: number;
  /** The untouched u16 when family == "raw". */
  raw?: number;
}

// MiscConditional enum (matches omni_pca.programs.MiscConditional).
// Each entry: { value, label }. NONE renders as "always" and NEVER as
// "never" — both common authoring patterns.
export const MISC_CONDITIONALS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 0,  label: "always" },
  { value: 1,  label: "never" },
  { value: 2,  label: "it is light outside" },
  { value: 3,  label: "it is dark outside" },
  { value: 4,  label: "phone line is dead" },
  { value: 5,  label: "phone is ringing" },
  { value: 6,  label: "phone is off hook" },
  { value: 7,  label: "phone is on hook" },
  { value: 8,  label: "AC power is off" },
  { value: 9,  label: "AC power is on" },
  { value: 10, label: "battery is low" },
  { value: 11, label: "battery is OK" },
  { value: 12, label: "energy cost is low" },
  { value: 13, label: "energy cost is mid" },
  { value: 14, label: "energy cost is high" },
  { value: 15, label: "energy cost is critical" },
];

// Security modes for the SEC family (matches enuSecurityMode order).
export const SECURITY_MODE_NAMES: ReadonlyArray<{ value: number; label: string }> = [
  { value: 0, label: "Off (disarmed)" },
  { value: 1, label: "Day" },
  { value: 2, label: "Night" },
  { value: 3, label: "Away" },
  { value: 4, label: "Vacation" },
  { value: 5, label: "Day Instant" },
  { value: 6, label: "Night Delayed" },
];

export function decodeCondition(cond: number): DecodedCondition {
  const decoded = decodeConditionLoose(cond);
  // Some bits aren't modelled (e.g. the arming-transition flag of the
  // SEC family). If re-encoding wouldn't give the same value back,
  // show it as raw rather than as something subtly different.
  return encodeCondition(decoded) === cond ? decoded : { family: "raw", raw: cond };
}

function decodeConditionLoose(cond: number): DecodedCondition {
  if (cond === 0) return { family: "none" };
  const family = (cond >> 8) & 0xFC;
  const active = (cond & 0x0200) !== 0;
  if (family === 0x00) {
    return { family: "misc", misc: cond & 0x0F };
  }
  if (family === 0x04) {
    return { family: "zone", index: cond & 0xFF, active };
  }
  if (family === 0x08) {
    return { family: "unit", index: cond & 0x01FF, active };
  }
  if (family === 0x0C) {
    return { family: "time", index: cond & 0xFF, active };
  }
  // SEC family (family >= 0x10): area in high nibble of upper byte,
  // mode in top nibble.
  return {
    family: "sec",
    index: (cond >> 8) & 0x0F,
    mode: (cond >> 12) & 0x07,
  };
}

export function encodeCondition(c: DecodedCondition): number {
  switch (c.family) {
    case "none":
      return 0;
    case "misc":
      return (c.misc ?? 0) & 0x0F;  // family 0x00, low nibble = misc
    case "zone": {
      const idx = (c.index ?? 0) & 0xFF;
      return 0x0400 | (c.active ? 0x0200 : 0) | idx;
    }
    case "unit": {
      const idx = (c.index ?? 0) & 0x01FF;
      return 0x0800 | (c.active ? 0x0200 : 0) | idx;
    }
    case "time": {
      const idx = (c.index ?? 0) & 0xFF;
      return 0x0C00 | (c.active ? 0x0200 : 0) | idx;
    }
    case "sec": {
      const area = (c.index ?? 1) & 0x0F;
      const mode = (c.mode ?? 0) & 0x07;
      return (mode << 12) | (area << 8);
    }
    case "raw":
      return (c.raw ?? 0) & 0xFFFF;
  }
}


// --------------------------------------------------------------------------
// Clausal chain (multi-record) editor types
// --------------------------------------------------------------------------


/** ProgramType values for the chain head/body/tail records. */
export const PROGRAM_TYPE_WHEN = 5;
export const PROGRAM_TYPE_AT = 6;
export const PROGRAM_TYPE_EVERY = 7;
export const PROGRAM_TYPE_AND = 8;
export const PROGRAM_TYPE_OR = 9;
export const PROGRAM_TYPE_THEN = 10;

/** Roles assigned by the backend's chain_members payload. */
export type ChainMemberRole = "head" | "condition" | "action";

export interface ChainMember {
  slot: number;
  role: ChainMemberRole;
  fields: ProgramFields;
}

/** Decoded view of a Traditional AND/OR record's condition.
 *
 * AND records use the SAME family encoding as compact-form cond, but
 * the bytes land in different ProgramFields slots:
 *
 *   family   = fields.cond & 0xFF              (disk byte 1)
 *   instance = (fields.cond2 >> 8) & 0xFF      (disk byte 3)
 *
 * The selector bit (`0x0200`) doesn't apply to AND records the same
 * way — instead the family byte's bit 1 (0x02) carries the
 * secure/not-ready or off/on selector. For example:
 *   0x04 = ZONE secure         0x06 = ZONE not-ready
 *   0x08 = CTRL off            0x0A = CTRL on
 *   0x0C = TIME disabled       0x0E = TIME enabled
 */
export function decodeAndCondition(fields: ProgramFields): DecodedCondition {
  // Read together, the family byte and the instance byte are the same
  // u16 a compact-form cond carries, so decode it the same way. (On a
  // real panel "AND IF dark" is family 0x00 with the misc code 3 in the
  // instance byte, and bit 0 of the family byte is bit 8 of a unit
  // number — both are lost if the two bytes are decoded separately.)
  return decodeCondition(andConditionWord(fields));
}

export function andConditionWord(fields: ProgramFields): number {
  return (((fields.cond ?? 0) & 0xFF) << 8) | (((fields.cond2 ?? 0) >> 8) & 0xFF);
}

export function encodeAndCondition(c: DecodedCondition): {
  cond: number; cond2: number;
} {
  const word = encodeCondition(c);
  return { cond: (word >> 8) & 0xFF, cond2: (word & 0xFF) << 8 };
}

/** True if the AND/OR record's op byte indicates a Structured-OP
 *  comparison (TEMP > 70 etc.) rather than the Traditional bit-packed
 *  condition. Structured records use entirely different field
 *  semantics; the editor in this pass renders them read-only.
 *
 *  OP byte lives at fields.cond >> 8 (disk byte 2). 0 = Traditional;
 *  1..9 = Structured (CondOP enum).
 */
export function isStructuredAnd(fields: ProgramFields): boolean {
  return (((fields.cond ?? 0) >> 8) & 0xFF) !== 0;
}

/** Build a fresh empty AND record (Traditional, NEVER condition). */
export function emptyAndRecord(): ProgramFields {
  return {
    prog_type: PROGRAM_TYPE_AND,
    cond: 0x00,      // family byte: OTHER
    cond2: 0x0100,   // instance byte: misc NEVER (0x01)
    cmd: 0, par: 0, pr2: 0,
    month: 0, day: 0, days: 0, hour: 0, minute: 0,
  };
}

/** Build a fresh empty OR record. Same shape as AND with a different
 *  prog_type — semantically starts a new group in the conditions list.
 */
export function emptyOrRecord(): ProgramFields {
  return { ...emptyAndRecord(), prog_type: PROGRAM_TYPE_OR };
}

/** Build a fresh empty THEN action record (Turn OFF unit 1). */
export function emptyThenRecord(firstUnit: number = 1): ProgramFields {
  return {
    prog_type: PROGRAM_TYPE_THEN,
    cmd: 0,        // UNIT_OFF
    par: 0,
    pr2: firstUnit,
    cond: 0, cond2: 0,
    month: 0, day: 0, days: 0, hour: 0, minute: 0,
  };
}


// --------------------------------------------------------------------------
// Structured-OP AND record editing.
//
// When ``and_op`` (= ``(cond >> 8) & 0xFF``) is non-zero, the record
// encodes ``Arg1 OP Arg2`` where Arg1 and Arg2 are typed references
// (Zone, Unit, Thermostat, Area, TimeDate, Constant) plus per-type
// field selectors. This is fundamentally a different shape from the
// Traditional encoding handled by decodeAndCondition above.
//
// Wire layout (from programs.py decoders + clsProgram.cs):
//
//   cond  high byte (>>8)  = and_op           (CondOP)
//   cond  low byte  (& FF) = and_arg1_argtype (CondArgType)
//   cond2 (whole u16)      = and_arg1_ix      (object index or 0)
//   cmd                    = and_arg1_field   (per-type field selector)
//   par                    = and_arg2_argtype (CondArgType — usually Constant)
//   pr2                    = and_arg2_ix      (constant value OR second object idx)
//   month                  = and_arg2_field   (per-type field selector for arg2)
//   day, days              = and_compconst    (BE u16 — extra constant, rarely used)
//
// Editor cuts:
//   * Arg1 and Arg2 both restricted to Constant / Zone / Unit /
//     Thermostat / Area / TimeDate. Anything else (Aux / Audio /
//     System / etc.) stays read-only.
//   * Non-zero CompConst stays read-only (rarely used; preserved on
//     save).
// --------------------------------------------------------------------------


// CondOP enum (matches omni_pca.programs.CondOP). 0=Traditional is
// excluded from the editor — picking it would switch to Traditional
// editing semantics.
export const COND_OPS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 1, label: "==" },
  { value: 2, label: "!=" },
  { value: 3, label: "<" },
  { value: 4, label: ">" },
  { value: 5, label: "is odd" },
  { value: 6, label: "is even" },
  { value: 7, label: "is multiple of" },
  { value: 8, label: "in (bitmask)" },
  { value: 9, label: "not in (bitmask)" },
];

/** True iff the operator only uses Arg1 (no Arg2). */
export function isUnaryOp(op: number): boolean {
  return op === 5 || op === 6;  // ODD, EVEN
}

// CondArgType enum (matches omni_pca.programs.CondArgType). Only the
// editor-supported subset; full list is in programs.py.
export const ARG_TYPES: ReadonlyArray<{
  value: number; label: string; kind: string | null;
}> = [
  { value: 0,  label: "Constant",       kind: null },
  { value: 2,  label: "Zone",           kind: "zone" },
  { value: 3,  label: "Unit",           kind: "unit" },
  { value: 4,  label: "Thermostat",     kind: "thermostat" },
  { value: 6,  label: "Area",           kind: "area" },
  { value: 7,  label: "Time / Date",    kind: null },  // no object picker
];

export function isEditableArg1Type(argtype: number): boolean {
  return [2, 3, 4, 6, 7].includes(argtype);
}

export function argTypeKind(argtype: number): string | null {
  const a = ARG_TYPES.find((x) => x.value === argtype);
  return a ? a.kind : null;
}

// Per-Arg1Type field menus. Numbers match omni_pca.programs enums
// (enuZoneField / enuUnitField / enuThermostatField / enuTimeDateField).
export const FIELDS_BY_TYPE: Readonly<Record<number, ReadonlyArray<{
  value: number; label: string;
}>>> = {
  // Zone (argtype 2) — enuZoneField
  2: [
    { value: 1, label: "Loop reading" },
    { value: 2, label: "Current state" },
    { value: 3, label: "Arming state" },
    { value: 4, label: "Alarm state" },
  ],
  // Unit (argtype 3) — enuUnitField
  3: [
    { value: 1, label: "Current state" },
    { value: 2, label: "Previous state" },
    { value: 3, label: "Timer" },
    { value: 4, label: "Level" },
  ],
  // Thermostat (argtype 4) — enuThermostatField
  4: [
    { value: 1,  label: "Current temperature" },
    { value: 2,  label: "Heat setpoint" },
    { value: 3,  label: "Cool setpoint" },
    { value: 4,  label: "System mode" },
    { value: 5,  label: "Fan mode" },
    { value: 6,  label: "Hold mode" },
    { value: 7,  label: "Freeze alarm" },
    { value: 8,  label: "Comm error" },
    { value: 9,  label: "Humidity" },
    { value: 10, label: "Humidify setpoint" },
    { value: 11, label: "Dehumidify setpoint" },
    { value: 12, label: "Outdoor temperature" },
    { value: 13, label: "System status" },
  ],
  // Area (argtype 6) — single useful field
  6: [
    { value: 1, label: "Security mode" },
  ],
  // TimeDate (argtype 7) — enuTimeDateField
  7: [
    { value: 2,  label: "Year" },
    { value: 3,  label: "Month" },
    { value: 4,  label: "Day" },
    { value: 5,  label: "Day of week (1=Mon..7=Sun)" },
    { value: 6,  label: "Time (minutes since midnight)" },
    { value: 8,  label: "Hour" },
    { value: 9,  label: "Minute" },
  ],
};

export interface DecodedStructuredAnd {
  op: number;              // CondOP value (1..9)
  arg1Type: number;        // CondArgType
  arg1Ix: number;          // 1-based object index (0 for TimeDate)
  arg1Field: number;       // per-type field
  arg2Type: number;        // CondArgType (locked to Constant in editor)
  arg2Ix: number;          // constant value OR second object index
  arg2Field: number;       // per-type field (usually 0 for constants)
  compConst: number;       // extra constant; preserved verbatim
}

export function decodeStructuredAnd(fields: ProgramFields): DecodedStructuredAnd {
  return {
    op:        ((fields.cond ?? 0) >> 8) & 0xFF,
    arg1Type:  (fields.cond ?? 0) & 0xFF,
    arg1Ix:    fields.cond2 ?? 0,
    arg1Field: fields.cmd ?? 0,
    arg2Type:  fields.par ?? 0,
    arg2Ix:    fields.pr2 ?? 0,
    arg2Field: fields.month ?? 0,
    compConst: ((fields.day ?? 0) << 8) | (fields.days ?? 0),
  };
}

export function encodeStructuredAnd(s: DecodedStructuredAnd): Partial<ProgramFields> {
  return {
    cond:  ((s.op & 0xFF) << 8) | (s.arg1Type & 0xFF),
    cond2: s.arg1Ix & 0xFFFF,
    cmd:   s.arg1Field & 0xFF,
    par:   s.arg2Type & 0xFF,
    pr2:   s.arg2Ix & 0xFFFF,
    month: s.arg2Field & 0xFF,
    day:   (s.compConst >> 8) & 0xFF,
    days:  s.compConst & 0xFF,
  };
}

/** True iff the structured AND record is in a shape the editor can
 *  fully drive. Arg1 must be one of the editable reference types;
 *  Arg2 must be Constant or one of the editable reference types
 *  (unary operators ignore Arg2 entirely). Non-zero compConst stays
 *  read-only — preserved on save but not exposed as a form control. */
export function isEditableStructuredAnd(s: DecodedStructuredAnd): boolean {
  if (!isEditableArg1Type(s.arg1Type)) return false;
  if (!isUnaryOp(s.op) && s.arg2Type !== 0 && !isEditableArg1Type(s.arg2Type)) {
    return false;
  }
  if (s.compConst !== 0) return false;
  return true;
}

/** HA's hass object — minimal surface we use. */
export interface Hass {
  connection: {
    sendMessagePromise<T>(msg: unknown): Promise<T>;
    subscribeEvents<T>(
      callback: (event: T) => void,
      eventType: string,
    ): Promise<() => Promise<void>>;
  };
  config?: {
    entries?: Record<string, unknown>;
  };
  // Whole hass is much larger; we only touch what we need.
}
