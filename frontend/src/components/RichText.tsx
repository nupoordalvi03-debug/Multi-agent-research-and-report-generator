import { Fragment, ReactNode } from "react";

const TOKEN = /((?:\[S\d{2,3}\])+)|\*\*(.+?)\*\*/g;

function inline(text: string, labels: Record<string, string>, style: string, onCite?: (sid: string) => void): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) {
      const sids = m[1].match(/S\d{2,3}/g) || [];
      const known = sids.filter((s) => labels[s]);
      out.push(
        <span key={m.index} className="cite-group">
          {style !== "IEEE" && known.length > 1 && "("}
          {known.map((sid, i) => (
            <Fragment key={sid}>
              {i > 0 && (style === "IEEE" ? ", " : "; ")}
              <button type="button" className="cite" onClick={() => onCite?.(sid)} title={`Trace ${sid}`}>
                {style !== "IEEE" && known.length > 1 ? labels[sid].replace(/^\(|\)$/g, "") : labels[sid]}
              </button>
            </Fragment>
          ))}
          {style !== "IEEE" && known.length > 1 && ")"}
        </span>,
      );
    } else if (m[2]) out.push(<strong key={m.index}>{m[2]}</strong>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function RichText({ content, labels = {}, style = "IEEE", onCite }: {
  content: string; labels?: Record<string, string>; style?: string; onCite?: (sid: string) => void;
}) {
  const blocks = content.trim().split(/\n\s*\n/);
  return (
    <>
      {blocks.map((b, i) => {
        const lines = b.split("\n").filter((l) => l.trim());
        if (lines.length && lines.every((l) => /^\s*[-*]\s+/.test(l))) {
          return <ul key={i}>{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-*]\s+/, ""), labels, style, onCite)}</li>)}</ul>;
        }
        return <p key={i}>{inline(lines.join(" "), labels, style, onCite)}</p>;
      })}
    </>
  );
}
