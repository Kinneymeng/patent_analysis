# Third-party notices

The files under `cnipa_upstream/` and `math_to_omml.py` are adapted from
[`handsomestWei/patent-disclosure-skill`](https://github.com/handsomestWei/patent-disclosure-skill),
licensed under the MIT License. The original copyright and license text are
included in `cnipa_upstream/LICENSE.txt`.

The adapted files retain the public CNIPA announcement-site parsing and
Playwright browser workflow. Application orchestration, result models,
validation, and user interaction are implemented by this project.

Portable builds include Mermaid 11.12.0 for offline rendering. Mermaid is
licensed under the MIT License; its license text is included in
`MERMAID_LICENSE.txt`. The pinned JavaScript runtime is downloaded and
integrity-checked at package build time. It is never downloaded at runtime.
