# Data Processing Proposal for Secure Server-Side Document Digitisation

Prepared by Eko India Financial Services for State Bank of India

Date: [DATE]
To: [Name, Designation], State Bank of India
From: [Name, Designation], Eko India Financial Services Pvt. Ltd.
Subject: Proposal and request for a Data Processing Agreement covering secure server-side OCR of account-outreach lists

## 1. Purpose

This document proposes a secure and DPDP-compliant method for digitising the account lists that SBI shares with its Customer Service Points (CSPs) for authorised customer outreach, and requests that SBI and Eko enter into a Data Processing Agreement (DPA) to govern it.

## 2. Background

Under the current authorised arrangement, SBI provides CSPs with lists of accounts that require customer follow-up, for example inoperative accounts. The CSP contacts these customers using the Eko platform. At present these lists are provided on paper or as scanned images, and are read into the platform on the CSP's local computer. These computers are low-powered, which limits both the speed and the accuracy of the text-extraction (OCR) step.

## 3. Proposed enhancement

Eko proposes to perform only the OCR step, meaning the conversion of the scanned list into text, on Eko's secure server, which has the computing capacity to do this quickly and accurately. All other steps, including customer messaging, remain on the CSP's local machine. There is no change to the content, the purpose, or the channel of customer communication, all of which remain exactly as already authorised.

## 4. Data protection safeguards

Eko will act strictly as a Data Processor on SBI's instructions, for the OCR step only, with the following safeguards:

1. Encryption in transit. Each document is encrypted on the CSP device before it is sent, and can be decrypted only by the processing service.
2. Zero retention. The document is processed in memory only. No image, no extracted text, and no customer identifier is written to disk, logged, cached, or retained after the result is returned.
3. No secondary use. The data is never used for analytics, model training, profiling, or any purpose other than the requested OCR.
4. Confidential processing. Eko will move the OCR step into a hardware-secured environment (confidential computing) in which even Eko's own administrators cannot access the data during processing.
5. Data localisation. All processing takes place on servers located in India, in line with RBI expectations.
6. Minimal metadata. Eko retains only non-personal operational metrics, such as the number of pages processed and the processing time, for service monitoring and billing.
7. Access control and audit. Access is authenticated and restricted per CSP, and an audit trail of access events, without any content, is maintained.

## 5. Roles under the Digital Personal Data Protection Act, 2023

1. SBI is the Data Fiduciary and determines the purpose of processing.
2. Eko is the Data Processor and processes personal data only on SBI's documented instructions.
3. The CSP conducts the outreach on SBI's behalf within this framework.

## 6. What we request from SBI

1. Review of this proposal by SBI's compliance and legal teams.
2. Execution of a Data Processing Agreement between SBI and Eko that sets out scope, safeguards, retention (nil), security controls, audit rights, and breach-notification obligations.
3. Confirmation of any additional SBI or RBI requirements that we should incorporate.

## 7. Assurances

Eko will not begin server-side processing of any customer data until the DPA is executed. Eko will support any security review, documentation, or audit that SBI requires as part of onboarding.

## 8. Contact

[Name, Designation]
Eko India Financial Services Pvt. Ltd.
[Email], [Phone]

This proposal sets out the intended arrangement and its safeguards. It is subject to a definitive Data Processing Agreement reviewed and approved by the legal teams of both parties.
