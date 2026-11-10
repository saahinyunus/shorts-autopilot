# Privacy Policy

_Effective date: 10 November 2026_

Shorts Autopilot ("the app") is a personal automation tool operated by one person for their own YouTube channels.
It is not offered to the public, and nobody other than the operator signs in to it.

## What the app accesses

When the operator signs in with Google, the app asks for one permission:

- **YouTube upload** (`https://www.googleapis.com/auth/youtube.upload`): to upload videos, and to set their title,
  description, tags, category and publishing schedule, on the YouTube channels that the operator owns.

The app does not read the operator's email, contacts, files, watch history, comments, subscribers or analytics.

## What the app stores

- The Google sign-in token (a refresh token) that lets the scheduled workflow upload without the operator being
  present. It is kept only as an encrypted GitHub Actions secret in the operator's own repository and is never
  printed in logs or published.
- A list of video titles and topics that were already published (`state/history.json`), used to avoid repeating
  topics. It contains no personal data.

## What the app does not do

- It does not collect, store or process personal data of viewers or any other third party.
- It does not sell, share or transfer Google user data to anyone, and it does not use it for advertising or for
  training AI models.
- It does not use Google user data for any purpose other than uploading the operator's own videos.

The app's use of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Revoking access

The operator can revoke the app's access at any time at <https://myaccount.google.com/permissions>. Deleting the
GitHub secret that holds the token also stops all uploads.

## Contact

Please open an issue in this repository for any privacy question.
