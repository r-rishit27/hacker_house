/**
 * The front door: nobody reaches a page without signing in, and each role lands where it works.
 *
 * This only *routes*. It reads the role out of the session token without checking the signature,
 * because it does not need to trust it: every API call is verified by the server, which answers
 * 401 to a forged or expired token and 403 to a role without the permission. A tampered cookie
 * gets a different landing page and then nothing it may not see.
 */

import { NextResponse, type NextRequest } from "next/server";

const COOKIE = "sentinel_session";
const STAFF_ONLY = /^\/(?:$|cases|approvals|ring|benchmark|showcase)/;

function roleOf(token: string): string | null {
  try {
    const body = token.split(".")[1] ?? "";
    const json = JSON.parse(atob(body.replace(/-/g, "+").replace(/_/g, "/")));
    if (typeof json.exp === "number" && json.exp * 1000 < Date.now()) return null;
    return typeof json.role === "string" ? json.role : null;
  } catch {
    return null;
  }
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const token = req.cookies.get(COOKIE)?.value;
  const role = token ? roleOf(token) : null;

  if (pathname === "/login") {
    // Already signed in: go home rather than sign in twice.
    if (role) return NextResponse.redirect(new URL(role === "regulator" ? "/regulator" : "/", req.url));
    return NextResponse.next();
  }
  if (!role) {
    const to = new URL("/login", req.url);
    if (pathname !== "/") to.searchParams.set("next", pathname + search);
    return NextResponse.redirect(to);
  }
  // The examiner reads the record; the investigator console is not theirs.
  if (role === "regulator" && STAFF_ONLY.test(pathname)) return NextResponse.redirect(new URL("/regulator", req.url));
  // The regulator overview is for examiners and the fraud manager (rbac.py: regulator.view).
  if (pathname === "/regulator" && role !== "regulator" && role !== "fraud_manager") {
    return NextResponse.redirect(new URL("/", req.url));
  }
  return NextResponse.next();
}

export const config = {
  // Pages only: API calls, the proxies and static files are checked by the services themselves.
  matcher: ["/((?!api/|v2/|_next/|logo.png|icon.png|favicon.ico).*)"],
};
