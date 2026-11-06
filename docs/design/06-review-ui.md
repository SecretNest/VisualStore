# 06 · Manual Review and UI

## 8.1 UI structure

A single-page application. The top bar holds a profile switcher. Routes look like `/p/<profile>/groups`; every business page runs in the context of the current profile. After login the user lands on the last used profile; with only one profile the switcher is hidden.

Only two pages exist outside a profile: the login page and **Profile management** (create, rename display name, delete, per-profile disk usage, process pool status).

Five main pages inside a profile:

1. **Overview**: per-Folder file counts and state distribution, pending groups by band, running and queued jobs, last backup time.
2. **Folder management**: create / edit / delete Folders, IgnoredDirectories, Extensions; trigger a scan manually.
3. **Review queue**: list of Groups with filters: band, Folder, status, contains byte-identical, minimum group size. Supports bulk rules (§8.4).
4. **Group detail**: the main workspace, see §8.3.
5. **Jobs and settings**: job list and logs, backup, trash, threshold bands, frame-sampling parameters, archive Folder priority order.

The optional work in [11 §15](11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments) adds a sixth page, the judgment review queue.

## 8.2 Default resolution rules

For one Group:

- If the group holds files from both an archive Folder and an inbox Folder: preselect deleting every inbox file and keeping the archive files.
- If every file is in an inbox: preselect keeping the first by path order and deleting the rest. Mark `open`; a human must look.
- If every file is in an archive: no preselection, mark `open`.
- If the group spans more than one archive Folder: the priority order configured in settings decides which to keep (v1's OrderedFolders semantics).

Byte-identical (`identical`) groups: after preselection by the rules above, execute immediately without confirmation and set `status = auto_resolved`. Automatic execution can be turned off in settings, leaving the preselection to await confirmation.

## 8.3 Group detail

- Left: member thumbnails with Folder role, path, size, resolution, distance to the nearest group member, and whether byte-identical.
- Centre: large comparison view. By default the selected member and its nearest neighbour side by side; video shows a frame strip, clicking a frame switches to it; an overlay / blink comparison mode shows fine differences.
- Each member has three states: keep / delete / undecided. Preselection fills in defaults.
- Edge judgments: with two members selected, their Pair can be marked `related_keep` or `unrelated`; `unrelated` triggers an immediate regroup of the group, which may split it.
- Keyboard: digits switch members, `k` keep, `d` delete, `u` unrelated, `Enter` confirm and go to the next group, `Esc` skip. The goal is three keystrokes for most groups.
- "Open original" streams the original file from the server for the browser to display; no local viewer is needed.
- Confirming enqueues `apply_action` and sets the group `resolved`.

## 8.4 Bulk rules

The review queue page can apply the default rules to the current filter result and execute at once, for example: "every near_duplicate group that contains an archive file, by default rules". This replaces v1's `foreach` over low-threshold results. Before executing, the UI shows how many files will be deleted and asks for confirmation.

## 8.5 Actions and trash

- **Delete**: by default move to `.visualstore-trash/{yyyy-mm}/` under the owning Folder's root, preserving the relative path, and write an Action record. Within one filesystem this is a rename and instant. Default retention is 30 days; `purge_trash` cleans up on schedule. Settings can switch to permanent deletion.
- **Restore**: find the original path through the Action record.
- **Move / rename**: change the disk and update File in sync, keeping id, Fingerprints, and Pairs. Fails without overwriting if a file of the same name exists at the target.
- **Archive move (`archive_move`)**: move all or selected directories of an inbox Folder into a target archive Folder, preserving relative paths. Requires that the inbox has no `open` groups; otherwise prompt first. Removes empty directories afterwards. Across filesystems it degrades to copy plus delete, with progress.
- **Clear empty directories**: remove empty directories inside a Folder, as the final step of archive move and deletion, also triggerable manually.

Every action performs the disk operation first and changes the database only on success; a disk failure is recorded as the job error and the database stays unchanged.
