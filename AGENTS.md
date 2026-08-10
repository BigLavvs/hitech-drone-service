# Hitech Drone Mapping Assessment - Development Priorities

This repository is for a time-constrained technical assessment, not a general-purpose production product.

The assessment requirements and the existing architecture are the source of truth.

## Documentation Compliance Gate

Before proposing, prompting, implementing, or approving every task, inspect the current implementation and reconcile the task against all applicable source documents:

- `docs/Hitech_Drone_Service_System_Architecture.docx`
- `docs/Hitech_Drone_Service_System_Implementation.docx`
- `docs/Hitech_Drone_Service_Db_Schema.docx`
- the assessment requirements and supporting documentation in `docs/`

For backend work, also read `docs/BACKEND_BUILD_GUIDE.md` as the deferred-work checklist and decision register. It does not override the source documents.

Do not rely only on task summaries or memory of an earlier review. Confirm the relevant architecture, implementation, database, API, security, workflow, and testing requirements before work begins.

Do not make a new architectural, product, security, data-model, API, or workflow decision when the documents have not already specified it. Instead, stop and report:

1. the undecided or conflicting point;
2. the relevant document(s) and requirement(s);
3. why implementation cannot safely choose on its own; and
4. the precise user decision needed.

Do not silently substitute a different technology, data model, API convention, authorization rule, workflow, or test approach. Record any temporary deviation explicitly and return to the documented decision before the affected backend or integration work starts.

## Primary Objective

Complete the required assessment functionality thoroughly, correctly, and demonstrably.

Prioritize in this order:

1. Explicit assessment requirements
2. Existing approved architecture
3. Required security and authorization behaviour
4. Required API and data flows
5. End-to-end functionality needed for demonstration
6. Clean maintainable implementation
7. Visual polish only where it materially improves the required workflow

## Do Not Waste Development Time

Do not implement or investigate requirements merely because they are considered generic software-development best practices.

Unless explicitly required by the assessment or necessary for the implemented functionality, do not spend significant effort on:

- mobile responsiveness
- tablet-specific layouts
- elaborate animations
- accessibility refinements beyond basic semantic and usable controls
- unnecessary visual polish
- performance optimization before there is an actual performance problem
- unnecessary abstraction
- speculative scalability
- features not mentioned in the assessment
- production infrastructure beyond the documented assessment architecture
- unnecessary test coverage for trivial presentation-only code

The assessment is the priority.

## Testing Policy

Testing must be proportional to risk and importance.

Always test when a change affects:

- authentication
- authorization
- project or resource ownership
- file security
- file validation
- upload behaviour
- survey state transitions
- approval or rejection rules
- audit immutability
- asynchronous processing
- database behaviour
- API contracts
- security-sensitive behaviour
- a significant integration point

For trivial presentation-only changes such as:

- CSS spacing
- typography
- static layout adjustments
- template markup changes with no logic
- purely visual styling

Do not run an unnecessarily broad test suite.

Use the cheapest meaningful verification available.

Examples:

- template syntax or render check when relevant
- Django system check when configuration or code structure changed
- targeted test when business logic changed
- full test suite only when the change is broad enough to justify it

Do not repeatedly run expensive full-suite verification after every small frontend change.

## Change Scope

Before implementing a task:

1. Inspect the existing implementation.
2. Identify exactly what is required.
3. Make the smallest clean change that satisfies the requirement.
4. Do not refactor unrelated code.
5. Do not add speculative features.
6. Do not fix unrelated issues unless they block the current task.

## Task Batching

Combine two or more tightly coupled implementation items into one Codex task when they share the same module, authorization rules, data flow, and focused verification target. Split work only when a security boundary, unresolved documented decision, external integration, or independently demonstrable workflow requires separate review.

Do not create artificial micro-steps that add prompt and review overhead without reducing implementation risk.

## Stop Conditions

When the requested requirement is satisfied and appropriately verified:

STOP.

Do not automatically continue improving the implementation.

Do not create additional work.

Report:

- what changed
- what was verified
- any blocking issue
- any genuinely assessment-relevant concern

Then stop.

## Decision Rule

When deciding whether to spend time on an issue, ask:

"Does this materially affect the assessment requirement, architecture, security, correctness, or demonstration?"

If the answer is no, do not spend meaningful development time on it.

If the answer is uncertain, report the issue rather than automatically implementing it.

## Important

Do not invent requirements.

Do not treat generic production best practices as assessment requirements.

Do not optimize for completeness at the expense of finishing the required functionality.

The goal is a complete, correct, demonstrable assessment implementation within limited development resources.

## Repository Guidance For Future Codex Sessions

Apply this file as active project policy for work in this repository.

This repository file is the intended mechanism for cross-session persistence of these instructions. Any Codex session opened against this workspace should treat this file as the canonical project-specific guidance unless the user explicitly overrides it.
