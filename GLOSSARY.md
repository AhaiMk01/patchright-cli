# patchright-cli

An anti-detect browser automation CLI: callers drive real Chrome through short commands sent to a long-running daemon.

## Sessions and browsers

**Session**:
A named handle on one browser context that callers drive with commands; selected with `-s`.
_Avoid_: Browser, instance, profile

**Default Session**:
The Session used when no `-s` is given.

**Launched Session**:
A Session whose Chrome patchright-cli started itself and therefore owns outright.

**Profile**:
A named, persistent store of browser state — cookies, storage, logins, history — that a Launched Session runs on; at most one Session uses a Profile at a time. Unless told otherwise, a Session's Profile is named after the Session.
_Avoid_: User data dir, profile directory, persistent context

**Attached Session**:
A Session connected to a Chrome that something else started, reached over CDP.
_Avoid_: Connected session, remote session

**Host**:
Whatever started and owns the Chrome an Attached Session connects to — a person, a script, or a product such as a workspace manager.
_Avoid_: External browser, owner

**Host Context**:
The Host's own browser context, carrying its profile's cookies, storage and logins; at most one Session may use a given Host Context.
_Avoid_: Default context, existing context, persistent context

**Isolated Context**:
A fresh browser context an Attached Session creates inside the Host's Chrome, sharing none of the Host's state.
_Avoid_: Incognito context

## Tabs and pages

**Page**:
One browser tab as Chrome sees it.
_Avoid_: Tab (except in the playwright-cli-compatible `tab-*` command names, which act on Pages)

**Tab**:
A named workspace inside a Session — its own Page, element refs and navigation history — selected with `--tab`.
_Avoid_: Lane, worker

**Default Tab**:
The Tab used when no `--tab` is given; it follows whichever Page is currently selected rather than owning one.

## Ending a Session

**Close**:
Ending a Session by destroying what it owns: its Tabs' Pages and, for a context it owns, the context itself.
_Avoid_: Quit, kill (except `kill-all`)

**Detach**:
Letting go of an Attached Session while leaving the Host's Chrome, and anything the Host owns, untouched.
_Avoid_: Disconnect, release
