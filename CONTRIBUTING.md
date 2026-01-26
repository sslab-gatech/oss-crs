# Contributing to OSS-CRS

Thank you for your interest and time in contributing to OSS-CRS!
For first steps you can run one of our currently available CRSs on real world projects, or try developing/integrating your own CRS.
We'd be glad accepting feedback on either use case.

We are also currently discussing architectural changes, and you are more than welcome to follow such discussions in our Github Issues.

## Reporting Issues

We use the (Github issue tracker)[https://github.com/sslab-gatech/oss-crs/issues] for tracking our tasks and bugs.
When reporting, please include:

### Observed and Expected Behavior

What you see v.s. what you expected to see.
This includes build errors, faulty runtime behavior, or deviations from our specification.

### Reproduction Steps

The list of commands run to trigger such behavior.
Usage of OSS-CRS heavily relies on state set up by different commands,
and so it is essential for us to recreate such state (directories, images, etc.).

### Environment

Any other information about your environment would help. Things like a differently configured LiteLLM proxy or Docker may contribute to issues we have not seen in our development environment.

## Contributing Code

If you have a feature or fix that you want to contribute, branch off main and create a pull request when ready!

Ideally if you are the only developer of said branch, please [rebase](https://git-scm.com/book/en/v2/Git-Branching-Rebasing) from main before creating your PR to keep git history clean.

For commit messages, we use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) as our standard.
`fix:`, `feat:`, `chore:`, `docs:`, and `refactor:` are types commonly used.

When you create a PR, assign a reviewer. Assign @azchin for the bug-finding component, and @grill66 for the bug-fixing component.
