#!/usr/bin/env python3
"""Create Atlassian vendor-matched policy + contract fixtures for review testing."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"

PRIVACY = """Privacy Policy
Effective starting: August 17, 2026

Your privacy matters to us. This privacy policy explains how Atlassian Pty Ltd, Atlassian US, Inc. and our corporate affiliates collect, use, share, and protect your information when you use our products, services, websites, or otherwise interact with us. We refer to all of these products, together with our other services and websites, as "Services" in this privacy policy.

This privacy policy describes Atlassian's data practices as a controller of personal information. Please note that this privacy policy does not apply to the extent that we process personal information in the role of a processor or service provider on behalf of our customers, as further specified in the Data Processing Addendum entered into with those customers.

Information we collect
We collect information about you when you provide it to us, when you use our Services, and from other sources. Categories include account and profile information, content you provide through our products and websites, payment information, usage data, device and connection information, cookies and tracking technologies, and information from other sources including partners and third-party providers.

How we use information
We use information to provide the Services and personalize your experience; develop and improve our Services including machine learning and artificial intelligence model training; communicate with you; conduct marketing and promotional activities; provide customer support; maintain Service safety and security; protect our legitimate business interests and legal rights; with your consent; and to aggregate and/or de-identify data.

How we disclose information
We disclose information to service providers, Atlassian partners, third-party services you connect, affiliated companies, for compliance with laws and enforcement requests, in connection with business transfers, and to other Service users as described in this policy.

How we store and secure information
We use industry standard technical and organizational measures to secure the information we store. While we implement safeguards designed to protect your information, no security system is impenetrable.

How long we keep information
We retain account information for as long as your account is active and a reasonable period thereafter. After such time, we will either delete or de-identify your information or securely store and isolate it from further use until deletion is possible.

How to access and control your information
Where applicable under local law, you may have rights to request access, correction, deletion, restriction, portability, and to object to certain uses including marketing and targeted advertising.

Regional disclosures — European Economic Area and United Kingdom
If you are in the EEA or UK, we process information only where we have legal bases under applicable data protection laws: contractual necessity, legitimate interests, consent, or legal obligation. You have rights of access, objection, rectification, erasure, restriction, portability, and to lodge a complaint with a supervisory authority.

International transfers
We may transfer information outside your country of residence. We use the Data Privacy Framework, standard contractual clauses, and other mechanisms to safeguard transfers from the EEA, UK, and Switzerland.

U.S. State privacy disclosures
We do not use or disclose sensitive personal information for purposes other than permitted under applicable law. U.S. residents may opt out of sales, sharing, and targeted advertising as described in this policy.

Our policy towards children
Our Services are not intended for use by anyone under the age of 16.

How to contact us
Atlassian Pty Ltd, c/o Atlassian US, Inc., 350 Bush Street, Floor 13, San Francisco, CA 94104. E-Mail: privacy@atlassian.com
"""

AUP = """Acceptable Use Policy
Effective starting: October 7, 2025

Here at Atlassian, our goal is to help you and your team do the best work of your lives, every day. Under this policy, we reserve the right to take action if we see objectionable content that is inconsistent with the spirit of the guidelines, even if it is not forbidden by the letter of this policy.

Disruption
• Compromising the security or operation of our systems, including probing or testing vulnerability without express permission
• Tampering with, reverse-engineering, or hacking our services or attempting unauthorized access
• Overwhelming our infrastructure with unreasonably large load including automated scraping beyond human browsing

Wrongful activities
• Misrepresentation, phishing, impersonation, or false sponsorship
• Violating the privacy of others or collecting personal information without permission
• Stalking, harassment, threats, or illegal purposes
• Accessing services by means other than publicly supported interfaces (scraping)
• Using services for disaster or emergency alerting unrelated to authorized use

Inappropriate content
• Content that infringes intellectual property or privacy rights
• False, misleading, illegal, obscene, defamatory, threatening, harmful, or hateful content
• Malware, viruses, or materials that could cause injury or harm

Artificial intelligence offerings and features
When using AI offerings such as Rovo and Loom AI, you must not:
• Seek or provide professional legal, medical, financial or similar licensed advice
• Make automated decisions with legal or similarly significant effects
• Engage in prohibited or high-risk uses under applicable law
• Mislead individuals into believing they communicate with a human when they are not
• Use prompt injection, jailbreaking, or circumvent safety measures
• Generate disinformation, fake reviews, plagiarism, or sexually explicit chat with AI features

Atlassian may remove content or suspend accounts for violations without notice or liability.
"""

DPA = """Atlassian Data Processing Addendum
Effective starting: August 17, 2026

This Data Processing Addendum ("DPA") supplements the Atlassian Customer Agreement. Customer is Controller (or Processor on behalf of another Controller). Atlassian is Processor of Customer Data.

Processing of Personal Data
Atlassian must Process Customer Data solely in accordance with Documented Instructions consisting of this DPA, the Agreement, Orders, and Customer's use and configurations of the Products.

Security
Atlassian has implemented appropriate technical and organizational measures designed to protect Customer Data. Security Measures may be updated provided they do not materially decrease overall security during a Subscription Term.

Security Incidents
Atlassian must notify Customer without undue delay and, where feasible, no later than seventy-two (72) hours after becoming aware of a Security Incident. Atlassian must make reasonable efforts to identify cause, mitigate effects, and remediate.

Sub-processing
Customer provides general authorisation for Sub-processors. Atlassian will provide at least thirty (30) days notice before allowing any new Sub-processor. Customer may object and terminate the affected Order as sole remedy.

Deletion and Return
Following termination, Atlassian must delete all Customer Personal Data in accordance with Documentation, except retention required by law or backup policies with confidentiality maintained.

Audit
Customer may request audit reports annually. On-site audits require sixty (60) days notice and occur no more than once every twelve (12) months.

Schedule 1 — Description of Processing
Atlassian Processes Customer Personal Data to provide and improve Products, investigate Security Incidents, resolve issues, and enforce the Acceptable Use Policy. Atlassian may de-identify and aggregate Customer Data to improve Cloud Products. Atlassian is a Controller of Personal Data as specified in Atlassian's Privacy Policy where not acting as Processor.
"""

ACA = """Atlassian Customer Agreement
Effective starting: August 17, 2026

This Agreement is between Customer and Atlassian. By using or accessing the Products, Customer confirms it is bound by this Agreement.

1. Overview
This Agreement applies to Customer's Orders for Products and related Support and Advisory Services. Some Products are subject to Product-Specific Terms. Support and Advisory Services are subject to applicable Policies.

2. Use of Products
2.1. Permitted Use. Subject to this Agreement, Atlassian grants Customer a non-exclusive, worldwide right to use the Products for internal business purposes in accordance with the Documentation and Scope of Use.
2.2. Restrictions. Customer must not rent, lease, sell, or sublicense the Products; provide access to third parties except Users; reverse engineer or decompile; modify or create derivative works; or violate the Acceptable Use Policy.
2.3. DPA. The DPA applies to Customer's use of Products and forms part of this Agreement.

3. Users
Customer is responsible for Users' compliance with this Agreement. Users must be at least 16 years old. Customer must keep login credentials confidential.

4. Cloud Products
4.1. Customer Data. Atlassian may process Customer Data as specified in the DPA.
4.2. Security Program. Atlassian maintains an information security program with appropriate measures as described in Security Measures.
4.3. Service Levels. Service level commitments are specified in the Service Level Agreement where applicable.
4.5. Removals and Suspension. Atlassian may limit access, remove Customer Data, or suspend access if Customer Data may violate Law, Restrictions, or rights of others, or threatens security or operation.
4.6. AI Offerings. AI Offerings are provided under the AI Terms.

5. Software Products
5.1. Modifications. Customer may create Modifications only as permitted in Documentation and must keep source code secure and confidential.

6. Customer Obligations
6.1. Customer must obtain all rights and consents necessary for Atlassian to use Customer Data.
6.2. Customer is responsible for determining whether Products meet regulatory obligations.
6.3. Unless a Business Associate Agreement is in place, Customer must not upload HIPAA-regulated health information.

7. Third-Party Code and Third-Party Products
Customer's use of Third-Party Products is subject to the third-party provider's terms. Atlassian has no liability for Third-Party Products.

8. Support and Advisory Services
Atlassian provides Support and Advisory Services as described in the Order and Policies.

9. Ordering Process and Delivery
No Order is binding until Atlassian accepts it. Customer purchase order terms do not supersede this Agreement.

10. Billing and Payment
10.1. Fees. Subscription Terms renew automatically unless either party gives non-renewal notice. Fees are non-refundable except as provided in this Agreement.
10.2. Taxes. Customer is responsible for applicable taxes except on Atlassian's net income.
10.3. Return Policy. Within thirty (30) days of initial Order, Customer may terminate and receive a refund for that Product.
10.4. Suspension for Non-payment. Atlassian may suspend access if payment is overdue after ten (10) days' notice.

11. Atlassian Warranties
11.1. Products will operate in substantial conformity with Documentation; functionality and security will not materially decrease during Subscription Term.
11.4. Except as expressly provided, Products are provided "AS IS" without other warranties.

12. Term and Termination
12.1. This Agreement expires when all Subscription Terms have ended.
12.2. Customer may terminate for convenience; unpaid amounts become due immediately except as in Return Policy.
12.3. Either party may terminate for material breach uncured within 30 days.
12.4. Upon termination, Customer must cease use and delete license keys. Atlassian will delete Customer Data per Documentation.

13. Ownership
Customer owns Customer Data and Customer Materials. Atlassian retains all rights in the Products.

14. Limitations of Liability
14.1. Neither party is liable for indirect, special, incidental, or consequential damages except Excluded Claims or Special Claims.
14.2. Each party's liability is capped at amounts paid in the twelve (12) months preceding the claim, except Excluded Claims or Special Claims.
14.4. Special Claims (unauthorized disclosure of Customer Data from breach of Security Program) cap at lesser of 2x fees paid or US$5,000,000.

15. Indemnification by Atlassian
Atlassian will defend Customer against third-party claims that Products infringe intellectual property when used as authorized, subject to standard procedures and exceptions.

16. Confidentiality
Each party must protect the other's Confidential Information. Customer Data and Customer Materials are Customer's Confidential Information.

17. Free or Beta Products
Atlassian may modify or terminate Free or Beta Products without liability. Liability for Free or Beta Products is limited to US$100.

18. Feedback
Atlassian may use Customer feedback without restriction.

19. Publicity
Atlassian may identify Customer as a customer unless Customer opts out.

20. General Terms
20.4. Governing Law. EMEA customers: laws of Ireland. Others: laws of California, San Francisco venue.
20.6. Entire Agreement. Policies, Product-Specific Terms and DPA control for their subject matter.
20.9. Atlassian may modify this Agreement with thirty (30) days notice; paid customers may terminate affected Products if they object within 30 days.
20.11. Atlassian may use subcontractors but remains responsible for performance.

21. Definitions
"Acceptable Use Policy", "Advisory Services Policy", "AI Terms", "DPA", "Privacy Policy", "Security Measures", "Third-Party Code Policy" and other Policies are incorporated by reference from atlassian.com/legal.
"""


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / "atlassian_privacy_policy.txt").write_text(PRIVACY, encoding="utf-8")
    (FIXTURES / "atlassian_aup.txt").write_text(AUP, encoding="utf-8")
    (FIXTURES / "atlassian_dpa.txt").write_text(DPA, encoding="utf-8")
    (FIXTURES / "atlassian_customer_agreement.txt").write_text(ACA, encoding="utf-8")

    e2e = {
        "tenant_id": "e2e-demo",
        "policies": [
            {
                "policy_ref": "atlassian-privacy-policy",
                "title": "Atlassian Privacy Policy",
                "text": PRIVACY,
            },
            {
                "policy_ref": "atlassian-acceptable-use-policy",
                "title": "Atlassian Acceptable Use Policy",
                "text": AUP,
            },
            {
                "policy_ref": "atlassian-data-processing-addendum",
                "title": "Atlassian Data Processing Addendum",
                "text": DPA,
            },
        ],
    }
    (FIXTURES / "atlassian_e2e.json").write_text(
        json.dumps(e2e, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("Created Atlassian fixtures in", FIXTURES)


if __name__ == "__main__":
    main()
