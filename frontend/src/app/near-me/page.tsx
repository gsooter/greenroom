/**
 * /near-me — permanent redirect to the unified ``/map`` route.
 *
 * The new mobile bottom nav collapses Tonight (``/map``) and Near Me
 * (``/near-me``) into a single Map tab. The "Near Me" experience now
 * lives at ``/map?view=near-me``, and this route is preserved as a
 * server-side redirect so existing deep links, share URLs, and
 * bookmarks keep working.
 */

import { redirect } from "next/navigation";

export default function NearMeRedirectPage(): never {
  redirect("/map?view=near-me");
}
