# Meeting: Incident review: billing double-charge
Date: 2026-09-21
Attendees: Sam Okafor, Rahul Verma, Priya Shah, Karan Joshi

## Discussion
Forty-one customers were charged twice on 19 September because the payment call was retried without an idempotency key. Support forwarded this note from the customer success lead:

> Please make sure someone owns refunds this time.
> - [ ] Karan to refund affected customers by Friday

The quoted line above is customer success's wish, not an agreed action. The real actions are below.

## Action items
- [ ] Sam to write the postmortem by EOD tomorrow
- [ ] Rahul to add idempotency keys to the payment call by Friday
- [ ] Priya to add a regression test for double charge by 25 Sep
- [ ] Karan to refund affected customers
