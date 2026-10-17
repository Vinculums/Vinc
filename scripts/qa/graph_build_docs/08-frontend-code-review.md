# Frontend code review guidelines

These guidelines describe what the Harbor frontend team checks in a pull request and how reviewers and authors work together. They cover the web app and the shared component library.

## Size and scope

A pull request should change one thing. Changes over 400 lines, excluding generated files and snapshots, are split unless the author explains why they cannot be. Refactoring and behavior changes go in separate pull requests so a reviewer can tell which lines change what users see.

## Response time

Reviewers respond within one working day. A first response can be a question or a request to split; it does not have to be an approval. Authors resolve every comment, either with a change or with a reply saying why not.

## What reviewers check

Reviewers check that components use semantic color tokens, never primitives, and that new text meets the contrast rule. They tab through any form or dialog the change touches, following the lessons of the settings form accessibility review. They check loading, empty and error states for every new data view. They look for a test that fails without the change.

## Tests

Logic in hooks and utilities needs unit tests. A new screen needs at least one end to end test of its main path. Snapshot tests alone are not accepted as coverage, because they pass after any change that someone approves without reading.

## Performance

A change that adds more than 20 KB to the main bundle after compression needs a note explaining why, and the bundle size check in CI comments the difference on the pull request. Lists that can exceed 200 rows use virtualization.

## Approval

One approval from a frontend team member is required, and two for changes to the shared component library. The author merges after approval and watches the deploy. Nobody approves their own change by pushing to a reviewer's branch.
