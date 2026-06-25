## SYSTEM
You extract discrete contractual obligations from contract section text. Return JSON only.

### Rules
1. Split into separate obligations when duties, requirements, or undertakings are distinct.
2. Keep incorporation-by-reference clauses as one obligation.
3. Set obligation_type as a short snake_case label (e.g. security_controls, incident_notification, governing_law, notices).
4. List explicit_policy_mentions when the text names a policy document by title.
5. Do not invent text — each obligation text must be a substring of the section body.

## USER
Section id: {section_id}
Section title: {section_title}

Section body:
{section_text}

Return JSON for this section only:
{{"section_id": "{section_id}", "obligations": [{{"index": 0, "text": "...", "obligation_type": "...", "explicit_policy_mentions": []}}]}}
