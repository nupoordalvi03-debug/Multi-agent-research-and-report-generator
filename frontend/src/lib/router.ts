import { useEffect, useState } from "react";

export type Route =
  | { name: "home" } | { name: "new" } | { name: "history" } | { name: "settings" }
  | { name: "research"; id: string; tab: string };

export function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "new") return { name: "new" };
  if (parts[0] === "history") return { name: "history" };
  if (parts[0] === "settings") return { name: "settings" };
  if (parts[0] === "research" && parts[1]) return { name: "research", id: parts[1], tab: parts[2] || "overview" };
  return { name: "home" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (path: string) => { window.location.hash = path; };
