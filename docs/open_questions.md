# Open questions

Questions raised by the sample file. The cleaner currently uses the default noted in each one; any of them can be changed in the config.

## For the customer

1. **Owners:** the export has no owner column. Who should own these contacts and accounts in Reevo? One rep, or a mapping by territory or account?
2. **LinkedIn:** `Personal Linkedin URL` and `linkedinURL` disagree on all 49 rows where both are filled (e.g. `/in/annaking` vs `/in/anna-king`). Which is authoritative? The tool currently prefers `Personal Linkedin URL`.
3. **Phone:** is Mobile the right primary number, or should it be Direct? The Holistic Industries "Office" number is the same for every contact. Is it a switchboard?
4. **Out-of-place records:** five rows (Apple, ASML, Applied Materials, including "Bruce Wayne" and "Martha / John") don't match the rest of this cannabis-operator list and have no website. Are they real prospects or test data?
5. **Email Quality:** 4 emails are marked "Email" rather than "Verified Email" (all at TerrAscend). Import them, or only verified ones?
6. **Column count:** the export has 73 columns, though we were told to expect ≤ 50. Is this the standard export view?
7. **Rejected rows:** 5 rows have no email/phone and/or no domain. Can the customer supply these, or should the rows be left out?

## For Reevo internally
- Phone format: is E.164 correct?
- Do extensions have a field?
- Is a contact with no account allowed, or is the Account required on every row?
