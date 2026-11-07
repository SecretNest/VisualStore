# Review workflow

## The queue

**Groups** lists groups of related files. Filters: status (`open` needs you; `auto resolved`
was executed by the rules; `resolved`; `dismissed`), Folder, band, byte-identical only, minimum
size. Bands come from the smallest distance inside the group: `identical` (same bytes),
`near duplicate` (≤ `similarity.near_duplicate`), `variant` (≤ `similarity.variant`),
`candidate`. Byte-identical groups are resolved automatically by default
(`resolution.auto_execute_identical`).

## Group detail: three keystrokes

The left column lists members with role, path, size, distance to the nearest member and a
thumbnail; the centre compares the selected member with its nearest neighbour.

| Key | Effect |
|---|---|
| `1`–`9`, `↑`/`↓` | select a member |
| `k` / `d` | keep / delete the selected member |
| `Enter` | confirm the decisions (deletions go to the trash) and open the next open group |
| `Esc` | skip to the next open group |
| `s` / `o` / `b` | side by side / overlay (opacity slider) / blink comparison |
| `n` / `p` | compare with the next / previous other member; `Space` swaps the two |
| `a` then `u` | mark the anchored and the selected member unrelated (the group splits) |

Default decisions follow the rules: archive files are kept and inbox files deleted when both
are present; inbox-only groups keep the first by path; archive-only groups wait for you. Most
groups therefore need only a look and `Enter`.

Videos and animated images show a **frame strip** under the comparison; click a frame to put it
into the comparison. "Open original" streams the file itself; "details" opens the file page.

## File detail

Shows the file, its group and its **candidate pairs**: files similar beyond the grouping
threshold but within `similarity.candidate_distance`. "Related, keep both" or "Unrelated" records
a judgment that grouping honours. Rename and move (to another Folder or directory) never
overwrite an existing file.

## Bulk rules

On the Groups page, **Default rules for this filter** applies the default decisions to every
open group matching the current filter. **Preview** reports how many groups match, how many get a
complete default resolution (the rest are skipped), how many files would be deleted and from
which Folders; **Execute** runs one job that re-checks each group before acting. When more than
`bulk.confirm_threshold` files (default 100) would be deleted you must type the number back. A
group a human has judged ("keep both") is never resolved by a bulk run.

## Trash

Deleted files go to `.visualstore-trash/<yyyy-mm>/` under their Folder root, keeping the
relative path, for `trash.retention_days` (30). **Jobs → Trash** lists them; **Restore** puts a
file back with its id, hashes and judgments and queues its vectors again.

Deleting or moving files can leave empty directories behind. **Folders → Clean empty
directories** queues a job that removes them bottom-up under that Folder; the trash directory
is never touched.
