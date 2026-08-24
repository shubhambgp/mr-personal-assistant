# Hand-authored Gmail API fixtures

Written to the shape `users.threads.get` returns, NOT captured from a real
mailbox. Every name and address is fictional and every product is one of the
invented brands, so these pass `check_no_vendor_terms.py` like any other
committed text file.

Anything captured from an actual inbox belongs in `../gmail.local/`, which is
gitignored. A real mailbox is a real person's data.

The set is chosen for the cases that are easy to get wrong:

| thread | why it is here |
|---|---|
| `t_needs_reply` | a doctor wrote last — the ordinary case |
| `t_follow_up` | the rep wrote last and nobody replied |
| `t_escalate` | a suspected adverse event: flag and route, never answer |
| `t_loud_newsletter` | shouts "URGENT" and asks for nothing |
| `t_injection` | tells the model to exfiltrate the doctor list |
