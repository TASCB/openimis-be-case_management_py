# openIMIS Backend Case Management module

Household case administration for the TASAF MIS: payment-change governance, household and
member deactivation with reasons, follow-up remarks, and the pending-data-update queue that
feeds the admin banner.

Module number **29** (rights `29xxxx`).

## Scope

This module is a **policy and evidence layer**. It owns no household, member or payment state:

| Fact | Owned by |
|---|---|
| Household, members, roles | `individual` — `Group`, `GroupIndividual` |
| Enrolment / active status | `social_protection` — `GroupBeneficiary.status` |
| Payment configuration | `tasaf_payment` — `PaymentAccount` |

Case Management records *decisions and events* over those, and enforces the business rules that
must bind every client — web and mobile alike — by living in the service layer rather than in a
mutation resolver.

Full specification: `docs/CASE_MANAGEMENT_WEB_ADMIN_PLAN.html` in the dist repo.

## Status

- **Phase 1 — database and models: implemented.**
- Phase 2 (services), Phase 3 (GraphQL), Phase 4 (notifications/approval wiring) pending.

## Design rules

1. **No state duplication.** New tables record events; current state is read from the owning module.
2. **Validation belongs to the service, never the view.** A rule enforced in a resolver protects
   the web form only; mobile queries the same schema and would bypass it.
3. **Events, not snapshots.**
4. Cross-module foreign keys are always `DO_NOTHING`.
