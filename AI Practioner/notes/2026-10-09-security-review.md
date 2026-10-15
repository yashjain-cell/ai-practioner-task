# Meeting: Security review
Date: 2026-10-09
Attendees: Sam Okafor, Rahul Verma, Priya Shah, Karan Joshi

## Discussion
The external pen-test report landed on Monday. Findings were mostly low severity but two need action this month: admin accounts without MFA and an outdated express dependency with a known advisory. Sam pasted the auditor's suggested format into the doc, which is why some lines below look like tasks but are not ours:

```
# example from the auditor's template
- [ ] Owner to remediate finding by <date>
```

<!-- - [ ] Sam to buy hardware tokens for everyone by Friday  (cut from the agenda, not agreed) -->

Karan confirmed the two ex-contractors no longer have access. The incident response document is still a stub. Everyone agreed the pen-test findings should not be shared outside this group.

## Action items
- [ ] Sam to enable MFA on all admin accounts by 2026-10-16
- [ ] Rahul to patch the outdated express dependency by Monday
- [ ] Priya to document the incident response steps by 23 Oct
- [x] Karan to revoke access for the two ex-contractors
