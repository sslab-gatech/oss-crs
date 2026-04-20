---
marp: true
theme: default
paginate: true
html: true
---

# Meeting Notes

OpenSSF Cyber Reasoning Systems Special Interest Group

---

## Agenda

1. Roadmap
2. Community Contributions
3. Atlantis-Java

---

## Roadmap (from 01/12 meeting)

<style scoped>h2 { font-size: 1.2em; margin-bottom: 0.2em; } img { max-height: 580px; display: block; margin: auto; }</style>

![Roadmap](./OSS-CRS%20Timeline%20OpenSSF%20Bi-Weekly%2001-12%20Slide%202.svg)

---

## Roadmap

<style scoped>input[type="checkbox"] { opacity: 1; transform: scale(1.3); margin-right: 8px; }</style>

<ul style="list-style: none; padding-left: 0;">
<li><input type="checkbox" checked disabled> Complete implementation of CRS benchmarks: <b>CRSBench project currently undergoing experiments</b></li>
<li><input type="checkbox" checked disabled> Unify bug-finding and bug-fixing features: <b>Complete as of <a href="https://github.com/ossf/oss-crs/pull/162">#162</a> (builder-sidecar)</b></li>
<li><input type="checkbox" checked disabled> Integration of AIxCC finalists' CRSs: <b>All AFC bug-finding CRSs integrated</b></li>
<li><input type="checkbox" disabled> Re-architect for remote deployment: <b>development blocked on Azure account provisioned by GT</b></li>
<li><input type="checkbox" disabled> Deploy for real-world bug finding and patching</li>
</ul>

---

## Required Items for Deployment

- **Triaging**: Add new CRS type to OSS-CRS pipeline
    - Clusterfuzz crash deduplication
    - Triagers from AIxCC CRSs
    - agentic triaging and report generation
- **Target Projects**: Selected suite of OSS projects that we run our CRSs against

---

## Atlantis-Java

Check out the blog and paper below to learn more about agentic sinkpoint-fuzzing and the performance of open weight LLMs! 

Atlantis-Java is updated and remains accessible through OSS-CRS

- [Team-Atlanta Blog: SinkFuzz GLM](https://team-atlanta.github.io/blog/post-sinkfuzz-glm/)
- [GONDAR arXiv paper](https://arxiv.org/abs/2604.01645)

---

## Community Contributions

- [#159](https://github.com/ossf/oss-crs/pull/159) : Warn users if resource config conflicts with machine resources (@tkqdldk)
- [#168](https://github.com/ossf/oss-crs/pull/168) : Docker Compose secrets for LLM keys (@tusharshah21)
- [#171](https://github.com/ossf/oss-crs/pull/171) : patchsense-crs semantic patch validator to registry (@aaronsrhodes)

---

## Q&A / Discussion

Refer to Cyber Reasoning Systems bi-weekly meeting notes.
