## Description: <br>
抖音公开数据智能分析工具。支持关键词搜索排序、抖人作品抓取、实时热榜获取，适用于短视频营销、竞品分析和热点监控，助力爆款内容策划与流量追踪。 <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[um-why](https://clawhub.ai/user/um-why) <br>

### License/Terms of Use: <br>
MIT <br>


## Use Case: <br>
External users and developers use this skill to search public Douyin videos by keyword, retrieve creator post data, and monitor hot-search trends for content planning, competitor analysis, and marketing reports. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Search keywords and creator URLs are sent to the GUAIKEI API service. <br>
Mitigation: Use the skill only for public Douyin data you are comfortable sharing with that service, and avoid sensitive or private query terms. <br>
Risk: GUAIKEI_API_TOKEN is a private API key. <br>
Mitigation: Store the token in an environment variable, do not commit it to source control, and rotate it if exposure is suspected. <br>
Risk: Returned search and competitor-analysis data can be retained in local log files. <br>
Mitigation: Review and delete the logs directory when retained Douyin results are no longer needed. <br>


## Reference(s): <br>
- [ClawHub skill page](https://clawhub.ai/um-why/douyin-search-keyword) <br>


## Skill Output: <br>
**Output Type(s):** [Text, Markdown, JSON, Shell commands, Configuration, Files] <br>
**Output Format:** [JSON or Markdown result summaries, with optional local JSON log files.] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Requires Node.js >=16.14.0 and GUAIKEI_API_TOKEN; result limits are 1-200 items where supported.] <br>

## Skill Version(s): <br>
1.1.3 (source: frontmatter and package.json) <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>
