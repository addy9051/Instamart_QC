# Privacy impact checklist (proof-of-pack, DPDP Act 2023)

Complete and get legal sign-off **before** enabling `INSTA_AUDIT_PROOF_ENABLED` with real footage. This is an engineering checklist, not legal advice.

## Purpose and scope
- [ ] Purpose is documented as order-completeness QC and dispute resolution (`order_completeness_qc`). It is never used to score, rank or discipline individual packers.
- [ ] Packer notice and consent/labour-agreement requirements reviewed with HR/legal for each store.
- [ ] Camera covers the bag mouth only; set `INSTA_AUDIT_PROOF_ROI` so faces and the rest of the station are cropped out.

## Minimisation (implemented in `insta_audit/privacy.py`)
- [ ] `INSTA_AUDIT_PROOF_ROI` configured and verified on a real frame.
- [ ] `INSTA_AUDIT_PROOF_BLUR_BOXES` covers printed customer-label zones; add a face detector adapter if faces can enter the ROI.
- [ ] Frames that cannot be decoded or redacted are dropped, never stored raw.
- [ ] Images are downscaled and metadata is stripped. Evidence records contain no worker identity; if one is needed, store only `pseudonymize()` output.

## Retention and access
- [ ] `INSTA_AUDIT_PROOF_RETENTION_DAYS` matches the refund/dispute window.
- [ ] A scheduler calls `POST /v1/admin/proof/purge` at least daily (and the bucket has a matching lifecycle rule).
- [ ] Bucket/directory access is limited to CX/support roles; access is logged; encryption at rest is on.
- [ ] `INSTA_AUDIT_PROOF_SIGNING_KEY` is set and kept outside the bundle store; head hashes appear in the structured audit log.

## Rights and incidents
- [ ] Process for access/erasure requests tied to an `order_id`.
- [ ] Breach-response owner named.
- [ ] Published notice describes what is captured, why, for how long, and who can see it.
