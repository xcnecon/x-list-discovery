"""Conservative person classification. Explicit profile reviews take precedence."""
import re

BD = re.compile(
    r"\b(?:bd|business development|marketing|growth (?:lead|manager|expert)|"
    r"head of growth|partnerships?|kol|koc|gtm|community manager|"
    r"customer (?:success|service)|account manager)\b|"
    r"商务|品牌经理|市场经理|客户经理|社区大使|大客户|返佣业务|渠道合作|代理合作|招商", re.I | re.ASCII)

PERSON = re.compile(
    r"\b(?:co[- ]?founder|founder|ceo|cio|cto|cfo|trader|investor|researcher|"
    r"engineer|developer|journalist|professor|writer|analyst|student|father|mother|"
    r"husband|wife|dad|mom|builder|degen|artist|photographer|filmmaker|"
    r"my (?:views|opinions|thoughts|portfolio)|personal (?:views|opinions|account)|"
    r"i (?:am|trade|build|work|write|invest))\b|"
    r"交易员|研究员|程序员|工程师|创始人|合伙人|个人观点|个人记录|个人言论|"
    r"个人备忘录|我(?:的|是|在)|韭菜|打工|主理人|博主|学生|投资人|独立交易", re.I | re.ASCII,
)
ORG = re.compile(
    r"\b(?:official (?:account|token|community)|(?:crypto|cryptocurrency) exchange|"
    r"(?:trading|analytics|intelligence|lending|liquidity|investment) platform|"
    r"(?:trading|crypto|financial) (?:app|terminal)|(?:a|the|our) (?:protocol|platform|exchange|launchpad)|"
    r"(?:we|we're|we are) (?:build|building|bring|bringing|help|helping|power|powering)|"
    r"(?:community[- ]run|community[- ]owned) (?:meme|token)|"
    r"(?:ca|contract address)\s*:|(?:non[- ]profit|foundation)|"
    r"(?:support|customer service)\s*[:：]|(?:news|media) (?:outlet|platform|agency))|"
    r"官方(?:账号|帐号|推特)|交易所官方|项目官方|代币官方|"
    r"一站式.*(?:平台|终端)|(?:我们|致力于).*(?:平台|协议|基础设施)", re.I,
)
ORG_NAME = re.compile(
    r"\b(?:foundation|protocol|exchange|labs|ventures|capital|news|media|dao)\b|"
    r"基金会|交易所|官方|新闻|研究院|实验室", re.I,
)


def classify(row, reviews):
    handle = row["userName"].lower()
    organizations = reviews.get("organizations", {})
    if handle in organizations:
        return "organization", organizations[handle]
    if handle in reviews.get("uncertain", {}):
        return "uncertain", reviews["uncertain"][handle]
    if handle in reviews.get("bd_accounts", {}):
        return "individual", reviews["bd_accounts"][handle]
    if handle in reviews.get("individuals", set()):
        return "individual", "manual_profile_review"
    bio = re.sub(r"https?://\S+|@\w+", " ", row.get("description") or "")
    if ORG.search(bio):
        return "organization", "organization_profile_language"
    if ORG_NAME.search(row.get("name") or ""):
        return "uncertain", "organization_like_name"
    if PERSON.search(bio):
        return "individual", "personal_profile_language"
    return "uncertain", "needs_manual_profile_review"
