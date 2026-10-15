# Meeting: Billing migration sync
Date: 2026-09-03
Attendees: Karan Joshi, Sam Okafor, Rahul Verma, Priya Shah

## Discussion
The invoice schema maps cleanly except for rounding on multi-currency invoices. Priya raised that rounding is the main test risk. Cutover date is not fixed yet.

## Action items
- [ ] Karan to export last quarter invoices by 10 Sep
- [ ] Rahul to review the schema mapping doc by Monday
- [ ] Priya to draft the test plan for invoice rounding by end of next week
- [ ] Sam to snapshot the production DB before cutover
