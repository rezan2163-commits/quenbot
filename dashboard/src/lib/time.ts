const DEFAULT_TIME_ZONE = process.env.NEXT_PUBLIC_QUENBOT_TIMEZONE || "Europe/Vienna";

type DateLike = string | number | Date | null | undefined;

function parseNumericTimestamp(value: number) {
  return new Date(value < 1_000_000_000_000 ? value * 1000 : value);
}

export function parseQuenbotDate(value: DateLike) {
  if (value instanceof Date) return new Date(value.getTime());
  if (typeof value === "number") return parseNumericTimestamp(value);
  if (typeof value !== "string") return new Date(Number.NaN);

  const trimmed = value.trim();
  if (!trimmed) return new Date(Number.NaN);

  if (/^\d+$/.test(trimmed)) {
    return parseNumericTimestamp(Number(trimmed));
  }

  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(trimmed)) {
    return new Date(trimmed);
  }

  return new Date(`${trimmed.replace(" ", "T")}Z`);
}

export function toTimestampMs(value: DateLike) {
  return parseQuenbotDate(value).getTime();
}

export function formatInQuenbotTimeZone(value: DateLike, options?: Intl.DateTimeFormatOptions) {
  const date = parseQuenbotDate(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("tr-TR", {
    timeZone: DEFAULT_TIME_ZONE,
    ...options,
  }).format(date);
}

export function formatTimeOnly(value: DateLike) {
  return formatInQuenbotTimeZone(value, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}