# SkillSwap First-Round Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the single-file SkillSwap MVP so Discover, Search, Matches, and Settings have distinct purposes, richer deterministic data, consistent cards, and the user-approved visual choices.

**Architecture:** Keep React, CSS, seed data, routing, state, and self-tests inside the existing root `index.html`. Add small pure helpers for normalization, country/city filters, sorting, relationship labels, and setting updates; page components consume those helpers while `normalizeState()` remains the only persistence boundary.

**Tech Stack:** React 18.3.1 UMD, ReactDOM 18.3.1 UMD, Babel Standalone 7.25.6, CSS, Hash routing, `localStorage`, GitHub Pages.

## Global Constraints

- All product code remains in root `index.html`; do not add a build system, package manager, backend, or application source files.
- Keep default Chinese and full English parity; every new translation key must exist in both dictionaries.
- Keep storage isolated to `skillswap-mvp-v1`; logout must not delete data and reset must remove only this key.
- Use exactly 28 seed skills and 12 seed partner users.
- Use fixed demo numbers and dates; do not introduce `Math.random()`.
- Preserve Hash navigation, browser refresh, Back/Forward, reduced-motion support, keyboard focus, and mobile navigation.
- Do not add `.DS_Store`, `.superpowers/`, or unrelated working-tree files to any commit.
- Every implementation task updates the inline `TEST_CASES` before production code and finishes with a focused commit.

## File Map

- Modify: `index.html` — all CSS, translations, deterministic data, helpers, routes, components, pages, state, and inline self-tests.
- Modify: `README.md` — final feature list, test count, routes, and GitHub Pages demo instructions.
- Reference: `docs/superpowers/specs/2026-08-16-skillswap-v2-improvements-design.md` — approved behavior and acceptance criteria.
- Track progress in: `docs/superpowers/plans/2026-08-16-skillswap-first-round-improvements.md` — check completed boxes during execution.

## Verified local completion record

- [x] Tasks 1–8: deterministic data, location-aware Search, contact-style Matches, rose landing, complete Settings, and local avatar handling are implemented in `index.html` with inline test coverage.
- [x] Task 9 local documentation: README feature guide and this completion record updated.
- [x] Task 9 browser visual QA: desktop and 390 × 844 mobile stories passed with no console errors or horizontal overflow; 47/47 inline self-tests pass.
- [x] Task 9 GitHub Pages publication: remote `main` published, Pages workflow succeeded, and the deployed `index.html` Git Blob SHA matches the verified local release.

---

### Task 1: Expand deterministic skills, locations, users, and translations

**Files:**
- Modify: `index.html:300-735`
- Test: `index.html:1120-1290`

**Interfaces:**
- Produces: `COUNTRIES`, `getCountryById(id)`, `getCitiesForCountry(countryId)`, exactly 28 `SKILLS`, exactly 12 `MOCK_USERS`, `PROFILE_LABELS`, `profileLabel(group,id,language)`, normalized profile metadata, and bilingual UI keys.
- Consumes: existing `t()`, `localized()`, `getSkillById()`, `cloneData()`.

- [ ] **Step 1: Add failing translation and seed-contract tests**

Add these cases to `TEST_CASES` before changing constants:

```jsx
test("first-round translations stay in parity", () => {
  const zhKeys = Object.keys(TRANSLATIONS.zh).sort().join("|");
  const enKeys = Object.keys(TRANSLATIONS.en).sort().join("|");
  assert(zhKeys === enKeys, "Translation keys diverged");
  ["settings", "country", "filterCountry", "sortNewest", "sortLiked", "viewFullProfile", "communityOverview", "trendingSkills"].forEach(key => {
    assert(TRANSLATIONS.zh[key] && TRANSLATIONS.en[key], `Missing translation: ${key}`);
  });
});

test("expanded seed catalog is complete and deterministic", () => {
  assert(SKILLS.length === 28, `Expected 28 skills, received ${SKILLS.length}`);
  assert(MOCK_USERS.length === 12, `Expected 12 users, received ${MOCK_USERS.length}`);
  assert(new Set(SKILLS.map(skill => skill.id)).size === 28, "Skill ids must be unique");
  assert(new Set(MOCK_USERS.map(user => user.id)).size === 12, "User ids must be unique");
  MOCK_USERS.forEach(user => {
    ["countryId", "cityId", "interests", "likes", "publishedAt", "availability", "meetingModes", "reliability", "memberSince"].forEach(key => {
      assert(user[key] !== undefined, `${user.id} missing ${key}`);
    });
  });
});

test("country catalog puts China first and resolves dependent cities", () => {
  assert(COUNTRIES[0].id === "cn", "China must be first");
  assert(getCitiesForCountry("cn").some(city => city.id === "tianjin"), "Tianjin missing");
  assert(getCitiesForCountry("us").some(city => city.id === "san-francisco"), "San Francisco missing");
  assert(getCitiesForCountry("missing").length === 0, "Unknown country should return no cities");
});
```

- [ ] **Step 2: Run self-tests and verify the new contracts fail**

Open `http://localhost:4173/?selftest=1`.

Expected: the three new cases fail because the translations, location catalog, 28 skills, and 12 complete users do not exist yet; the original 24 cases remain passing.

- [ ] **Step 3: Add the exact location and skill catalogs**

Add this catalog next to `SKILLS` and expand `SKILLS` to the listed ids:

```jsx
const COUNTRIES = Object.freeze([
  { id:"cn", zh:"中国", en:"China", cities:[
    ["beijing","北京","Beijing"], ["shanghai","上海","Shanghai"], ["tianjin","天津","Tianjin"],
    ["guangzhou","广州","Guangzhou"], ["shenzhen","深圳","Shenzhen"], ["hangzhou","杭州","Hangzhou"],
    ["chengdu","成都","Chengdu"], ["nanjing","南京","Nanjing"], ["wuhan","武汉","Wuhan"], ["xian","西安","Xi'an"]
  ]},
  { id:"us", zh:"美国", en:"United States", cities:[["new-york","纽约","New York"],["san-francisco","旧金山","San Francisco"],["los-angeles","洛杉矶","Los Angeles"],["boston","波士顿","Boston"],["seattle","西雅图","Seattle"]] },
  { id:"uk", zh:"英国", en:"United Kingdom", cities:[["london","伦敦","London"],["manchester","曼彻斯特","Manchester"],["edinburgh","爱丁堡","Edinburgh"]] },
  { id:"ca", zh:"加拿大", en:"Canada", cities:[["toronto","多伦多","Toronto"],["vancouver","温哥华","Vancouver"],["montreal","蒙特利尔","Montreal"]] },
  { id:"au", zh:"澳大利亚", en:"Australia", cities:[["sydney","悉尼","Sydney"],["melbourne","墨尔本","Melbourne"]] },
  { id:"jp", zh:"日本", en:"Japan", cities:[["tokyo","东京","Tokyo"],["osaka","大阪","Osaka"]] },
  { id:"kr", zh:"韩国", en:"South Korea", cities:[["seoul","首尔","Seoul"],["busan","釜山","Busan"]] },
  { id:"sg", zh:"新加坡", en:"Singapore", cities:[["singapore","新加坡","Singapore"]] }
].map(country => Object.freeze({...country, cities:Object.freeze(country.cities.map(([id,zh,en]) => Object.freeze({id,zh,en}))) })));

function getCountryById(id) { return COUNTRIES.find(country => country.id === id) || null; }
function getCitiesForCountry(countryId) { return getCountryById(countryId)?.cities || []; }

const EXTRA_SKILLS = [
  ["ui-design","creative","UI 设计","UI Design"], ["product-design","creative","产品设计","Product Design"],
  ["latte-art","lifestyle","咖啡拉花","Latte Art"], ["baking","lifestyle","烘焙","Baking"],
  ["public-speaking","academic","公众演讲","Public Speaking"], ["tennis","sports","网球","Tennis"],
  ["calligraphy","creative","书法","Calligraphy"], ["excel","technology","Excel","Excel"],
  ["japanese","academic","日语","Japanese"], ["french","academic","法语","French"],
  ["yoga","sports","瑜伽","Yoga"], ["illustration","creative","插画","Illustration"],
  ["personal-finance","lifestyle","基础理财","Personal Finance"], ["pottery","creative","陶艺","Pottery"]
];
```

Append `EXTRA_SKILLS.map(([id,category,zh,en]) => ({id,category,zh,en}))` to the existing 14 entries before freezing the 28-item array.

- [ ] **Step 4: Expand users and add exact profile metadata**

Add these exact five partners so `MOCK_USERS` contains 12 entries:

```jsx
function seedSkill(owner,skillId,level,zh,en){return {id:`${owner}-${skillId}`,skillId,level,desc:{zh,en}};}
const EXTRA_USERS=Object.freeze([
  {id:"leah-01",name:"Leah Xu",age:22,countryId:"cn",cityId:"hangzhou",languages:["zh","en"],interests:["design-systems","museums","tea"],likes:286,publishedAt:"2026-08-15T06:20:00.000Z",availability:["weekday-evening","sun-afternoon"],meetingModes:["public-place","online"],reliability:97,memberSince:"2026-02-11T00:00:00.000Z",avatar:"https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=500&q=80",bio:{zh:"产品设计师，喜欢把复杂流程整理得简单清楚。",en:"Product designer who enjoys making complex flows feel simple."},skillsOffered:[seedSkill("leah","ui-design","advanced","界面层级、组件和可用性。","Hierarchy, components, and usability."),seedSkill("leah","calligraphy","intermediate","硬笔基础和日常练习。","Pen calligraphy basics and daily practice.")],skillsWanted:[seedSkill("leah-want","english","intermediate","练习更自然的工作表达。","Practice natural workplace conversation."),seedSkill("leah-want","tennis","complete-beginner","从握拍和正手开始。","Start with grip and forehand.")]},
  {id:"omar-01",name:"Omar Khan",age:24,countryId:"uk",cityId:"london",languages:["en"],interests:["debate","startups","food"],likes:412,publishedAt:"2026-08-12T17:00:00.000Z",availability:["sat-morning","sun-evening"],meetingModes:["online","public-place"],reliability:99,memberSince:"2025-12-06T00:00:00.000Z",avatar:"https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?auto=format&fit=crop&w=500&q=80",bio:{zh:"创业社群主持人，擅长把表达变得有结构。",en:"Startup community host who makes communication more structured."},skillsOffered:[seedSkill("omar","public-speaking","advanced","演讲结构和临场表达。","Speech structure and confident delivery."),seedSkill("omar","personal-finance","intermediate","个人预算和储蓄基础。","Personal budgeting and saving basics.")],skillsWanted:[seedSkill("omar-want","english","intermediate","希望练习更清晰的写作。","I want to sharpen my writing."),seedSkill("omar-want","cooking","beginner","想学三道简单中餐。","Learn three simple Chinese dishes.")]},
  {id:"yuki-01",name:"Yuki Sato",age:20,countryId:"jp",cityId:"tokyo",languages:["ja","en"],interests:["manga","cafes","travel"],likes:365,publishedAt:"2026-08-16T03:10:00.000Z",availability:["weekday-evening","sat-afternoon"],meetingModes:["online","public-place"],reliability:96,memberSince:"2026-04-09T00:00:00.000Z",avatar:"https://images.unsplash.com/photo-1524504388940-b1c1722653e1?auto=format&fit=crop&w=500&q=80",bio:{zh:"插画学生，也喜欢带朋友练习实用日语。",en:"Illustration student who enjoys helping friends practice useful Japanese."},skillsOffered:[seedSkill("yuki","japanese","advanced","旅行与日常会话。","Travel and everyday conversation."),seedSkill("yuki","illustration","advanced","角色造型和配色。","Character shapes and color choices.")],skillsWanted:[seedSkill("yuki-want","photography","beginner","想拍好夜间街景。","Learn better night street photos."),seedSkill("yuki-want","latte-art","complete-beginner","从牛奶打发开始。","Start with steaming milk.")]},
  {id:"maya-01",name:"Maya Tremblay",age:23,countryId:"ca",cityId:"toronto",languages:["en","fr"],interests:["wellness","books","baking"],likes:244,publishedAt:"2026-08-11T14:45:00.000Z",availability:["sat-afternoon","sun-morning"],meetingModes:["online","public-place"],reliability:98,memberSince:"2026-01-22T00:00:00.000Z",avatar:"https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=500&q=80",bio:{zh:"双语编辑和瑜伽爱好者，喜欢温和但持续的学习。",en:"Bilingual editor and yoga enthusiast who values steady learning."},skillsOffered:[seedSkill("maya","french","advanced","法语发音和基础对话。","French pronunciation and basic conversation."),seedSkill("maya","yoga","intermediate","适合初学者的舒展练习。","Beginner-friendly mobility practice.")],skillsWanted:[seedSkill("maya-want","product-design","beginner","理解产品流程和原型。","Understand product flows and prototypes."),seedSkill("maya-want","baking","beginner","想掌握基础面包。","Learn a reliable basic loaf.")]},
  {id:"ethan-01",name:"Ethan Brooks",age:25,countryId:"us",cityId:"san-francisco",languages:["en"],interests:["data","cycling","ceramics"],likes:521,publishedAt:"2026-08-13T20:00:00.000Z",availability:["weekday-evening","sat-morning"],meetingModes:["online","public-place"],reliability:99,memberSince:"2025-11-17T00:00:00.000Z",avatar:"https://images.unsplash.com/photo-1531123897727-8f129e1688ce?auto=format&fit=crop&w=500&q=80",bio:{zh:"数据分析师，喜欢把实用工具教给第一次接触的人。",en:"Data analyst who enjoys teaching useful tools to first-time learners."},skillsOffered:[seedSkill("ethan","excel","advanced","公式、透视表和数据清理。","Formulas, pivots, and data cleanup."),seedSkill("ethan","python","intermediate","用 Python 自动化重复工作。","Automate repetitive work with Python.")],skillsWanted:[seedSkill("ethan-want","pottery","complete-beginner","想完成第一个小杯子。","Make my first small cup."),seedSkill("ethan-want","public-speaking","beginner","减少演示时的紧张。","Feel calmer during presentations.")]}
]);
```

Normalize all existing seed users to the same shape. For example:

```jsx
{
  id:"alice-01", name:"Alice Chen", age:18, countryId:"cn", cityId:"tianjin",
  languages:["zh","en"], interests:["street-photography","coffee","city-walks"],
  likes:328, publishedAt:"2026-08-14T09:30:00.000Z",
  availability:["sat-afternoon","sun-morning"], meetingModes:["public-place","online"],
  reliability:98, memberSince:"2026-03-18T00:00:00.000Z",
  avatar:"https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=500&q=80",
  bio:{zh:"喜欢用照片记录城市里安静的瞬间。",en:"I love documenting quiet moments around the city."},
  skillsOffered:[
    {id:"alice-photography",skillId:"photography",level:"advanced",desc:{zh:"人像、构图与手动曝光。",en:"Portraits, composition, and manual exposure."}},
    {id:"alice-editing",skillId:"video-editing",level:"intermediate",desc:{zh:"短视频节奏和基础调色。",en:"Short-form pacing and basic color work."}}
  ],
  skillsWanted:[
    {id:"alice-chemistry",skillId:"chemistry",level:"beginner",desc:{zh:"想补齐基础化学知识。",en:"I want to strengthen my chemistry basics."}},
    {id:"alice-cooking",skillId:"cooking",level:"beginner",desc:{zh:"想学简单的家常菜。",en:"I want to learn simple home cooking."}}
  ]
}
```

Set `DEMO_USER` to `countryId:"cn"`, `cityId:"tianjin"`, `interests:["chemistry","fitness","badminton"]`, `likes:476`, `publishedAt:"2026-08-10T08:00:00.000Z`, `availability:["weekday-evening","sun-afternoon"]`, `meetingModes:["public-place","online"]`, `reliability:99`, and `memberSince:"2025-10-12T00:00:00.000Z"`. Task 2 replaces all legacy `user.city` reads with `locationLabel()`.

Merge these exact metadata values into the seven existing users:

```jsx
const EXISTING_USER_META=Object.freeze({
  "alice-01":{countryId:"cn",cityId:"tianjin",interests:["street-photography","coffee","city-walks"],likes:328,publishedAt:"2026-08-14T09:30:00.000Z",availability:["sat-afternoon","sun-morning"],meetingModes:["public-place","online"],reliability:98,memberSince:"2026-03-18T00:00:00.000Z"},
  "bob-01":{countryId:"cn",cityId:"beijing",interests:["cooking","basketball","markets"],likes:294,publishedAt:"2026-08-09T10:00:00.000Z",availability:["sat-morning","sun-afternoon"],meetingModes:["public-place"],reliability:95,memberSince:"2026-02-03T00:00:00.000Z"},
  "mika-01":{countryId:"cn",cityId:"shanghai",interests:["indie-music","bands","songwriting"],likes:219,publishedAt:"2026-08-08T11:40:00.000Z",availability:["weekday-evening","sun-afternoon"],meetingModes:["online","public-place"],reliability:94,memberSince:"2026-05-12T00:00:00.000Z"},
  "jun-01":{countryId:"kr",cityId:"seoul",interests:["electronics","coding","prototypes"],likes:451,publishedAt:"2026-08-15T13:15:00.000Z",availability:["weekday-evening","sat-afternoon"],meetingModes:["online"],reliability:99,memberSince:"2025-12-19T00:00:00.000Z"},
  "lina-01":{countryId:"cn",cityId:"hangzhou",interests:["plants","drawing","journaling"],likes:187,publishedAt:"2026-08-06T08:50:00.000Z",availability:["sat-afternoon","sun-morning"],meetingModes:["public-place"],reliability:96,memberSince:"2026-06-01T00:00:00.000Z"},
  "sarah-01":{countryId:"cn",cityId:"tianjin",interests:["debate","languages","photography"],likes:337,publishedAt:"2026-08-13T07:30:00.000Z",availability:["weekday-evening","sun-afternoon"],meetingModes:["public-place","online"],reliability:98,memberSince:"2026-01-08T00:00:00.000Z"},
  "nora-01":{countryId:"cn",cityId:"tianjin",interests:["fitness","science","video"],likes:263,publishedAt:"2026-08-07T12:10:00.000Z",availability:["sat-morning","sun-morning"],meetingModes:["public-place","online"],reliability:97,memberSince:"2026-04-24T00:00:00.000Z"}
});
```

Add deterministic bilingual labels used by profile details:

```jsx
const PROFILE_LABELS=Object.freeze({
  availability:{"weekday-evening":{zh:"工作日晚间",en:"Weekday evenings"},"sat-morning":{zh:"周六上午",en:"Saturday morning"},"sat-afternoon":{zh:"周六下午",en:"Saturday afternoon"},"sun-morning":{zh:"周日上午",en:"Sunday morning"},"sun-afternoon":{zh:"周日下午",en:"Sunday afternoon"},"sun-evening":{zh:"周日晚间",en:"Sunday evening"}},
  meeting:{"public-place":{zh:"公共场所见面",en:"Meet in a public place"},online:{zh:"线上",en:"Online"}},
  interests:{"street-photography":{zh:"街头摄影",en:"Street photography"},coffee:{zh:"咖啡",en:"Coffee"},"city-walks":{zh:"城市漫步",en:"City walks"},cooking:{zh:"烹饪",en:"Cooking"},basketball:{zh:"篮球",en:"Basketball"},markets:{zh:"市集",en:"Markets"},"indie-music":{zh:"独立音乐",en:"Indie music"},bands:{zh:"乐队",en:"Bands"},songwriting:{zh:"写歌",en:"Songwriting"},electronics:{zh:"电子制作",en:"Electronics"},coding:{zh:"编程",en:"Coding"},prototypes:{zh:"原型",en:"Prototypes"},plants:{zh:"植物",en:"Plants"},drawing:{zh:"绘画",en:"Drawing"},journaling:{zh:"手账",en:"Journaling"},debate:{zh:"辩论",en:"Debate"},languages:{zh:"语言",en:"Languages"},photography:{zh:"摄影",en:"Photography"},fitness:{zh:"健身",en:"Fitness"},science:{zh:"科学",en:"Science"},video:{zh:"视频",en:"Video"},"design-systems":{zh:"设计系统",en:"Design systems"},museums:{zh:"博物馆",en:"Museums"},tea:{zh:"茶",en:"Tea"},startups:{zh:"创业",en:"Startups"},food:{zh:"美食",en:"Food"},manga:{zh:"漫画",en:"Manga"},cafes:{zh:"咖啡馆",en:"Cafes"},travel:{zh:"旅行",en:"Travel"},wellness:{zh:"身心健康",en:"Wellness"},books:{zh:"阅读",en:"Books"},baking:{zh:"烘焙",en:"Baking"},data:{zh:"数据",en:"Data"},cycling:{zh:"骑行",en:"Cycling"},ceramics:{zh:"陶艺",en:"Ceramics"},chemistry:{zh:"化学",en:"Chemistry"},badminton:{zh:"羽毛球",en:"Badminton"}}
});
function profileLabel(group,id,language){return PROFILE_LABELS[group]?.[id]?.[language]||id;}
```

- [ ] **Step 5: Add bilingual copy and rerun tests**

Insert these exact bilingual key groups into the existing dictionaries; keep the key set identical:

```jsx
// zh
settings:"设置",country:"国家",filterCountry:"所有国家",chooseCountryFirst:"请先选择国家",sortNewest:"最近发布",sortLiked:"最受好评",viewFullProfile:"查看完整资料",communityOverview:"社区概览",demoCommunityData:"演示社区数据",onlineNow:"人在线",totalUsers:"位用户",swapsToday:"今日交换",trendingSkills:"本周热门技能",demoTrend:"演示趋势",peopleSearching:"{count} 人正在寻找",recentSearches:"最近搜索",noRecentSearches:"搜索后会显示在这里",skillCategories:"技能分类",popularSearches:"热门关键词",canTeachSkill:"可以教你：{skill}",matchesFilters:"符合你的筛选条件",likedBy:"{count} 人好评",published:"发布于 {date}",complementaryMatches:"互补匹配",favoritePeople:"收藏的人",swapRequests:"交换请求",interests:"兴趣",availability:"可约时间",meetingPreference:"见面方式",reliability:"可靠度",joined:"加入时间",profileAndAvatar:"个人资料与头像",skillsAndGoals:"技能与学习目标",availabilityAndPlaces:"可约时间与地点",notificationSettings:"通知设置",privacyAndSafety:"隐私与安全",helpAndFeedback:"帮助与反馈",logout:"退出登录",logoutConfirm:"退出当前账号？资料会保留在本机。",profileVisibility:"资料可见性",showOnlineStatus:"显示在线状态",publicPlacesOnly:"仅允许公共地点",feedbackPlaceholder:"告诉我们哪里可以做得更好",feedbackSaved:"反馈已保存在本机演示中。",avatarTypeError:"请选择 JPEG、PNG 或 WebP 图片。",avatarSizeError:"图片不能超过 5 MB。",avatarProcessError:"无法处理这张图片，请换一张。",changeAvatar:"更换头像",category_technology:"技术",category_creative:"创作",category_academic:"学习",category_sports:"运动",category_lifestyle:"生活"

// en
settings:"Settings",country:"Country",filterCountry:"All countries",chooseCountryFirst:"Choose a country first",sortNewest:"Newest",sortLiked:"Most liked",viewFullProfile:"View full profile",communityOverview:"Community overview",demoCommunityData:"Demo community data",onlineNow:"online",totalUsers:"users",swapsToday:"swaps today",trendingSkills:"Trending this week",demoTrend:"Demo trend",peopleSearching:"{count} people searching",recentSearches:"Recent searches",noRecentSearches:"Your searches will appear here",skillCategories:"Skill categories",popularSearches:"Popular searches",canTeachSkill:"Can teach you: {skill}",matchesFilters:"Matches your filters",likedBy:"Liked by {count}",published:"Published {date}",complementaryMatches:"Complementary matches",favoritePeople:"Saved people",swapRequests:"Swap requests",interests:"Interests",availability:"Availability",meetingPreference:"Meeting preference",reliability:"Reliability",joined:"Joined",profileAndAvatar:"Profile & avatar",skillsAndGoals:"Skills & learning goals",availabilityAndPlaces:"Availability & places",notificationSettings:"Notifications",privacyAndSafety:"Privacy & safety",helpAndFeedback:"Help & feedback",logout:"Log out",logoutConfirm:"Log out now? Your profile stays on this device.",profileVisibility:"Profile visibility",showOnlineStatus:"Show online status",publicPlacesOnly:"Public places only",feedbackPlaceholder:"Tell us what could be better",feedbackSaved:"Feedback saved in this local demo.",avatarTypeError:"Choose a JPEG, PNG, or WebP image.",avatarSizeError:"Images must be 5 MB or smaller.",avatarProcessError:"We could not process that image. Try another one.",changeAvatar:"Change avatar",category_technology:"Technology",category_creative:"Creative",category_academic:"Learning",category_sports:"Sports",category_lifestyle:"Lifestyle"
```

Run `http://localhost:4173/?selftest=1`.

Expected: all original and three new cases pass; `SKILLS.length` is 28 and `MOCK_USERS.length` is 12.

- [ ] **Step 6: Commit deterministic data and copy**

```bash
git add index.html
git commit -m "feat: expand SkillSwap community data"
```

---

### Task 2: Migrate state and add pure data helpers

**Files:**
- Modify: `index.html:740-1060`
- Test: `index.html:1120-1290`

**Interfaces:**
- Produces: `normalizeUser(user)`, expanded `createInitialState()`, `normalizeState(value)`, `locationLabel(user, language)`, `sortUsers(users, sort)`, `formatPublishedAt(iso, language, now)`, `hasActiveSearchFilters(filters)`.
- Consumes: `COUNTRIES`, `getCountryById()`, `getCitiesForCountry()`, `cloneData()`.

- [ ] **Step 1: Add failing migration and helper tests**

```jsx
test("old state receives safe first-round defaults", () => {
  const old = normalizeState({version:1,language:"zh",isAuthenticated:true,currentUser:{id:"legacy",name:"Legacy",city:"Tianjin",skillsOffered:[],skillsWanted:[]}});
  assert(old.currentUser.countryId === "cn" && old.currentUser.cityId === "tianjin", "Legacy location not migrated");
  assert(old.recentSearches.length === 0, "Recent searches default missing");
  assert(old.notificationSettings.swapRequests === true, "Notification default missing");
  assert(old.privacySettings.publicPlacesOnly === true, "Safety default missing");
  assert(old.session.loggedIn === true, "Session migration failed");
});

test("location and sorting helpers are deterministic", () => {
  assert(locationLabel({countryId:"cn",cityId:"tianjin"},"zh") === "中国 · 天津", "Chinese location wrong");
  const sample=[{id:"a",likes:10,publishedAt:"2026-08-10T00:00:00Z"},{id:"b",likes:20,publishedAt:"2026-08-09T00:00:00Z"}];
  assert(sortUsers(sample,"liked")[0].id === "b", "Liked sort failed");
  assert(sortUsers(sample,"newest")[0].id === "a", "Newest sort failed");
  assert(sample[0].id === "a", "sortUsers mutated input");
});

test("active search filter detection ignores sort alone", () => {
  assert(hasActiveSearchFilters({q:"",level:"",country:"",city:"",lang:"",sort:"newest"}) === false, "Sort should not activate results");
  assert(hasActiveSearchFilters({q:"摄影",level:"",country:"",city:"",lang:"",sort:"newest"}) === true, "Query should activate results");
});
```

- [ ] **Step 2: Run self-tests and verify the migration cases fail**

Open `http://localhost:4173/?selftest=1`.

Expected: failures mention `normalizeUser`, `recentSearches`, `notificationSettings`, or missing helper functions.

- [ ] **Step 3: Implement normalization and state defaults**

```jsx
const DEFAULT_NOTIFICATIONS = Object.freeze({swapRequests:true,favoriteActivity:true,sessionReminders:true});
const DEFAULT_PRIVACY = Object.freeze({profileVisibility:"community",showOnlineStatus:true,publicPlacesOnly:true});

function legacyLocation(user={}) {
  const legacy=(user.city||"").toLocaleLowerCase();
  for (const country of COUNTRIES) {
    const city=country.cities.find(item => item.en.toLocaleLowerCase()===legacy || item.zh===user.city);
    if (city) return {countryId:country.id,cityId:city.id};
  }
  return {countryId:"cn",cityId:"tianjin"};
}

function normalizeUser(user={}) {
  const fallback=legacyLocation(user);
  return {
    ...cloneData(user),
    countryId:user.countryId||fallback.countryId,
    cityId:user.cityId||fallback.cityId,
    interests:Array.isArray(user.interests)?user.interests.filter(item=>typeof item==="string"):[],
    likes:Number.isFinite(user.likes)?user.likes:0,
    publishedAt:user.publishedAt||"2026-08-01T00:00:00.000Z",
    availability:Array.isArray(user.availability)?user.availability:[],
    meetingModes:Array.isArray(user.meetingModes)?user.meetingModes:["public-place"],
    reliability:Number.isFinite(user.reliability)?user.reliability:100,
    memberSince:user.memberSince||"2026-08-01T00:00:00.000Z",
    avatarDataUrl:typeof user.avatarDataUrl==="string"?user.avatarDataUrl:""
  };
}
```

Replace the state constructors with these exact fields and merge rules:

```jsx
function createInitialState(){return {version:STATE_VERSION,language:DEFAULT_LANGUAGE,isAuthenticated:false,currentUser:null,onboardingCompleted:false,onboardingStep:"profile",favorites:[],sentRequests:[],connections:[],editedSkills:[],recentSearches:[],notificationSettings:cloneData(DEFAULT_NOTIFICATIONS),privacySettings:cloneData(DEFAULT_PRIVACY),feedbackEntries:[],session:{loggedIn:false}};}
function normalizeState(value){
  const base=createInitialState(); if(!value||typeof value!=="object")return base;
  const loggedIn=value.session?.loggedIn===true||value.isAuthenticated===true;
  return {...base,version:STATE_VERSION,language:["zh","en"].includes(value.language)?value.language:base.language,isAuthenticated:loggedIn,currentUser:value.currentUser&&typeof value.currentUser==="object"?normalizeUser(value.currentUser):null,onboardingCompleted:value.onboardingCompleted===true,onboardingStep:["profile","share","learn","complete"].includes(value.onboardingStep)?value.onboardingStep:base.onboardingStep,favorites:Array.isArray(value.favorites)?value.favorites.filter(item=>typeof item==="string"):[],sentRequests:Array.isArray(value.sentRequests)?value.sentRequests.filter(item=>item&&typeof item==="object"):[],connections:Array.isArray(value.connections)?value.connections.filter(item=>typeof item==="string"):[],editedSkills:Array.isArray(value.editedSkills)?cloneData(value.editedSkills):[],recentSearches:Array.isArray(value.recentSearches)?[...new Set(value.recentSearches.filter(item=>typeof item==="string"))].slice(0,5):[],notificationSettings:{...base.notificationSettings,...Object.fromEntries(Object.entries(value.notificationSettings||{}).filter(([,item])=>typeof item==="boolean"))},privacySettings:{...base.privacySettings,...value.privacySettings},feedbackEntries:Array.isArray(value.feedbackEntries)?value.feedbackEntries.filter(item=>item&&typeof item==="object"):[],session:{loggedIn}};
}
```

- [ ] **Step 4: Implement pure display and sorting helpers**

```jsx
function locationLabel(user, language) {
  const country=getCountryById(user?.countryId);
  const city=country?.cities.find(item=>item.id===user?.cityId);
  return [country?.[language],city?.[language]].filter(Boolean).join(" · ");
}

function sortUsers(users, sort="newest") {
  return [...users].sort((a,b) => sort==="liked"
    ? (b.likes-a.likes)||Date.parse(b.publishedAt)-Date.parse(a.publishedAt)||a.id.localeCompare(b.id)
    : Date.parse(b.publishedAt)-Date.parse(a.publishedAt)||a.id.localeCompare(b.id));
}

function formatPublishedAt(iso, language, now=new Date("2026-08-16T12:00:00.000Z")) {
  const days=Math.max(0,Math.floor((now-Date.parse(iso))/86400000));
  if(days===0)return language==="zh"?"今天":"Today";
  if(days<7)return language==="zh"?`${days} 天前`:`${days} days ago`;
  return new Intl.DateTimeFormat(language==="zh"?"zh-CN":"en-US",{year:"numeric",month:"short",day:"numeric"}).format(new Date(iso));
}

function hasActiveSearchFilters(filters={}) {
  return [filters.q,filters.level,filters.country,filters.city,filters.lang].some(value=>String(value||"").trim());
}
```

- [ ] **Step 5: Rerun tests and commit the migration boundary**

Open `http://localhost:4173/?selftest=1`.

Expected: all tests pass; the browser console has no state-recovery errors when old `skillswap-mvp-v1` data is present.

```bash
git add index.html
git commit -m "feat: migrate SkillSwap profile state"
```

---

### Task 3: Simplify the landing page and restyle the flowing tags

**Files:**
- Modify: `index.html:20-270`
- Modify: `index.html:1280-1325`
- Modify: `index.html:1435-1450`
- Test: `index.html:1120-1290`

**Interfaces:**
- Produces: `MARQUEE_ROWS`, `COMMUNITY_STATS`, `CommunityStats`, updated `SkillMarquee`, simplified `LandingPage`.
- Consumes: `SKILLS`, `SkillTag`, `navigate()`, `t()`.

- [ ] **Step 1: Add failing visual-data contract tests**

```jsx
test("landing visual data matches the approved direction", () => {
  assert(MARQUEE_ROWS.length===2 && MARQUEE_ROWS.every(row=>row.length===14), "Marquee must show two rows of 14 skills");
  assert(new Set(MARQUEE_ROWS.flat()).size===28, "Every skill should appear once before duplication");
  assert(COMMUNITY_STATS.online===128 && COMMUNITY_STATS.users===3842 && COMMUNITY_STATS.swapsToday===46, "Community demo stats changed");
});
```

- [ ] **Step 2: Run tests and verify the constants are missing**

Expected: `landing visual data matches the approved direction` fails with `MARQUEE_ROWS is not defined`.

- [ ] **Step 3: Implement rose-only tags and slower motion**

Define the exact two rows and replace category colors with four rose tokens selected by stable `index % 4`; do not use yellow, purple, or saturated coral:

```jsx
const MARQUEE_ROWS=Object.freeze([
  Object.freeze(["photography","python","cooking","guitar","chemistry","fitness","drawing","ui-design","latte-art","tennis","excel","japanese","yoga","personal-finance"]),
  Object.freeze(["english","badminton","video-editing","arduino","writing","basketball","planting","product-design","baking","public-speaking","calligraphy","french","illustration","pottery"])
]);
```

Update CSS:

```css
.skill-tag{background:var(--tag-cream);border-color:rgba(255,255,255,.82);color:#241d20}
.skill-tag[data-tone="1"]{background:#f8e9ed}.skill-tag[data-tone="2"]{background:#e9c1ce}
.skill-tag[data-tone="3"]{background:#fff7f2}.skill-tag[data-tone="4"]{background:#d7a0b5}
.marquee-track{animation:marquee 33s linear infinite}.marquee.reverse .marquee-track{animation-duration:40s;animation-direction:reverse}
.marquee-zone:hover .marquee-track,.marquee-zone:focus-within .marquee-track{animation-play-state:paused}
@media (prefers-reduced-motion:reduce){.marquee-track{animation:none;transform:none}}
```

Pass `tone={(index%4)+1}` from `SkillMarquee` into `SkillTag`, and render it as `data-tone`.

- [ ] **Step 4: Remove duplicate hero actions and replace the black card**

Delete only the three-button row inside the hero content. Keep the header login and account creation buttons. Add:

```jsx
const COMMUNITY_STATS=Object.freeze({online:128,users:3842,swapsToday:46});
function CommunityStats({language}){
  return <section className="black-card community-stats"><div><p className="eyebrow">LIVE DEMO</p><h2>{t(language,"communityOverview")}</h2><p>{t(language,"demoCommunityData")}</p></div><div className="stat-row"><strong>{COMMUNITY_STATS.online}</strong><span>{t(language,"onlineNow")}</span><strong>{COMMUNITY_STATS.users.toLocaleString()}</strong><span>{t(language,"totalUsers")}</span><strong>{COMMUNITY_STATS.swapsToday}</strong><span>{t(language,"swapsToday")}</span></div></section>;
}
```

Replace the old `socialGallery` black card with `<CommunityStats language={language}/>`.

- [ ] **Step 5: Run tests and visually verify landing**

Open `http://localhost:4173/?selftest=1`; expected: all tests pass.

Open `http://localhost:4173/` at desktop width and 390 px width; expected: the hero has no duplicate three-button row, the header still offers login/signup, all 28 tags use rose tones, hover pauses them, and the community card shows three labeled demo numbers without overflow.

- [ ] **Step 6: Commit landing changes**

```bash
git add index.html
git commit -m "feat: refine SkillSwap landing experience"
```

---

### Task 4: Replace SkillLoop with trending skills

**Files:**
- Modify: `index.html:720-750`
- Modify: `index.html:1480-1505`
- Test: `index.html:1120-1290`

**Interfaces:**
- Produces: `TRENDING_SKILLS`, `TrendingSkills({language})`.
- Removes: `SKILL_LOOP`, `SkillLoopCard`, `skillLoop`, `demoMatch`, and `sharedTime` presentation keys.
- Consumes: `getSkillName()`, `navigate()`, `t()`.

- [ ] **Step 1: Add a failing trend contract test**

```jsx
test("trending skill demo data is fixed and searchable", () => {
  assert(TRENDING_SKILLS.map(item=>`${item.skillId}:${item.count}`).join("|")==="photography:128|english:96|python:74|ui-design:63|video-editing:51", "Trending data changed");
  TRENDING_SKILLS.forEach(item=>assert(getSkillById(item.skillId),`Missing trending skill ${item.skillId}`));
});
```

- [ ] **Step 2: Run tests and confirm `TRENDING_SKILLS` is missing**

Expected: the new trend case fails and all earlier tests pass.

- [ ] **Step 3: Delete SkillLoop data/UI and add the compact trend module**

```jsx
const TRENDING_SKILLS=Object.freeze([
  {skillId:"photography",count:128}, {skillId:"english",count:96}, {skillId:"python",count:74},
  {skillId:"ui-design",count:63}, {skillId:"video-editing",count:51}
]);

function TrendingSkills({language}){
  const max=TRENDING_SKILLS[0].count;
  return <section className="section trending-card glass"><div className="section-head"><div><p className="eyebrow">TRENDING</p><h2 className="section-title">{t(language,"trendingSkills")}</h2></div><span className="demo-chip">{t(language,"demoTrend")}</span></div><div className="trend-list">{TRENDING_SKILLS.map(item=><button className="trend-row" type="button" key={item.skillId} onClick={()=>navigate("#/search",{q:item.skillId})}><span>{getSkillName(item.skillId,language)}</span><span>{t(language,"peopleSearching",{count:item.count})}</span><i style={{width:`${Math.round(item.count/max*100)}%`}}/></button>)}</div></section>;
}
```

Render `<TrendingSkills language={language}/>` after peer buddies. Remove `.loop-*` CSS and add compact `.trending-*` styles capped at roughly half the former SkillLoop height.

- [ ] **Step 4: Run tests and verify navigation**

Expected: all self-tests pass. Clicking Photography opens `#/search?q=photography`; Back returns to the same Discover scroll flow; no visible `SkillLoop` copy remains in Chinese or English.

- [ ] **Step 5: Commit the Discover replacement**

```bash
git add index.html
git commit -m "feat: replace SkillLoop with skill trends"
```

---

### Task 5: Rebuild Search filters, empty state, sorting, and result cards

**Files:**
- Modify: `index.html:80-220`
- Modify: `index.html:1000-1050`
- Modify: `index.html:1350-1390`
- Modify: `index.html:1490-1520`
- Test: `index.html:1120-1290`

**Interfaces:**
- Produces: updated `filterUsers(users,filters,language)`, `rememberSearch(list,value)`, `relationshipSummary(currentUser,user,language)`, `SearchEmptyState`, `SearchResultCard`, rebuilt `SearchPage`.
- Consumes: `getCitiesForCountry()`, `sortUsers()`, `formatPublishedAt()`, `hasActiveSearchFilters()`, `locationLabel()`, `scoreComplementaryMatch()`.

- [ ] **Step 1: Add failing filter, history, and relationship tests**

```jsx
test("search filters country before city and exposes only two sorts", () => {
  const china=filterUsers(MOCK_USERS,{country:"cn",city:"tianjin"},"zh");
  assert(china.length>0 && china.every(user=>user.countryId==="cn"&&user.cityId==="tianjin"), "Country/city filter leaked");
  assert(sortUsers(MOCK_USERS,"newest")[0].publishedAt>=sortUsers(MOCK_USERS,"newest")[1].publishedAt, "Newest order wrong");
  assert(sortUsers(MOCK_USERS,"liked")[0].likes>=sortUsers(MOCK_USERS,"liked")[1].likes, "Liked order wrong");
});

test("recent search history is unique and capped", () => {
  const history=["Python","摄影","烹饪","网球","吉他"];
  assert(rememberSearch(history,"Python").join("|")==="Python|摄影|烹饪|网球|吉他", "Duplicate search not moved cleanly");
  assert(rememberSearch(history,"Excel").join("|")==="Excel|Python|摄影|烹饪|网球", "Search cap failed");
});

test("every search user receives a visible relationship summary", () => {
  MOCK_USERS.forEach(user=>assert(relationshipSummary(DEMO_USER,user,"zh").label.length>0,`${user.id} lacks relationship copy`));
});
```

- [ ] **Step 2: Run tests and confirm the new helper cases fail**

Expected: failures name `rememberSearch`, `relationshipSummary`, or missing country filtering.

- [ ] **Step 3: Implement filter and history helpers**

Update `filterUsers()` so country and city are independent exact-id checks; include interest text and localized location text in the query haystack; preserve the existing behavior that a recognized skill query searches offered skills. Remove its final name sort and let `sortUsers()` own ordering.

```jsx
function rememberSearch(list,value){
  const clean=String(value||"").trim();
  if(!clean)return list.slice(0,5);
  return [clean,...list.filter(item=>item.toLocaleLowerCase()!==clean.toLocaleLowerCase())].slice(0,5);
}

function relationshipSummary(currentUser,user,language){
  const match=scoreComplementaryMatch(currentUser,user);
  if(match.reciprocal)return {type:"reciprocal",label:`${t(language,"twoWay")} · ${match.score}%`};
  const offered=user.skillsOffered.find(item=>currentUser.skillsWanted.some(wanted=>wanted.skillId===item.skillId));
  if(offered)return {type:"teaches",label:t(language,"canTeachSkill",{skill:getSkillName(offered.skillId,language)})};
  return {type:"filtered",label:t(language,"matchesFilters")};
}
```

- [ ] **Step 4: Build the URL-backed filter bar and empty state**

`SearchPage` uses `{q,level,country,city,lang,sort}` with default `sort:"newest"`. Changing country writes `country` and clears `city`. The city `<select>` is disabled when `country` is empty and its options come only from `getCitiesForCountry(filters.country)`. The sort select contains only `newest` and `liked`.

Before active search, render:

```jsx
function SearchEmptyState({state,language,onSearch}){
  const categories=["technology","creative","academic","sports","lifestyle"];
  const popular=["photography","python","ui-design","english","cooking","tennis"];
  return <section className="search-starters"><div className="starter-card glass"><h2>{t(language,"recentSearches")}</h2><div className="skill-pills">{state.recentSearches.length?state.recentSearches.map(item=><button type="button" className="mini-pill" key={item} onClick={()=>onSearch(item)}>{item}</button>):<p className="muted">{t(language,"noRecentSearches")}</p>}</div></div><div className="starter-card glass"><h2>{t(language,"skillCategories")}</h2>{categories.map(category=><span className="mini-pill" key={category}>{t(language,`category_${category}`)}</span>)}</div><div className="starter-card glass"><h2>{t(language,"popularSearches")}</h2>{popular.map(skillId=><button type="button" className="mini-pill" key={skillId} onClick={()=>onSearch(skillId)}>{getSkillName(skillId,language)}</button>)}</div></section>;
}
```

Commit a recent search only on form submit, popular/recent chip click, or Enter; do not write one history item per keystroke.

- [ ] **Step 5: Replace result `UserCard` use with equal-height `SearchResultCard`**

```jsx
function SearchResultCard({user,currentUser,language,saved,onSave,fromHash}){
  const relationship=relationshipSummary(currentUser,user,language);
  return <article className="user-card search-result-card"><div className="card-top"><Avatar user={user}/><div><h3 className="user-name">{user.name}</h3><p className="user-meta">{locationLabel(user,language)}</p></div></div><p className="user-bio">{localized(user.bio,language)}</p><div className="skill-pills">{user.skillsOffered.slice(0,2).map(item=><span className="mini-pill" key={item.id}>{getSkillName(item.skillId,language)}</span>)}</div><div className="result-card-footer"><span className={`match-badge ${relationship.type}`}>{relationship.label}</span><p className="rating-line">♥ {t(language,"likedBy",{count:user.likes})} · {t(language,"published",{date:formatPublishedAt(user.publishedAt,language)})}</p><div className="card-actions"><button className="icon-btn" type="button" onClick={()=>onSave(user.id)} aria-label={saved?t(language,"saved"):t(language,"save")}>{saved?"♥":"♡"}</button><Button small onClick={()=>navigate(`#/people/${user.id}`,{from:fromHash})}>{t(language,"viewProfile")} →</Button></div></div></article>;
}
```

Use CSS Grid rows `auto auto auto 1fr` and `margin-top:auto` on `.result-card-footer`; every result card must have the footer even when no reciprocal match exists.

- [ ] **Step 6: Rerun functional and responsive checks**

Expected self-tests: all pass.

Browser checks:

- `#/search` shows starters, not people.
- Selecting China enables only Chinese cities; switching to Japan clears the previous city.
- `sort=newest` and `sort=liked` reorder deterministically.
- A query displays equal-height cards with relation, likes, date, and aligned buttons.
- Refresh and Back preserve Hash filters.

- [ ] **Step 7: Commit Search redesign**

```bash
git add index.html
git commit -m "feat: separate and refine SkillSwap search"
```

---

### Task 6: Turn Matches into a contact directory and enrich profiles

**Files:**
- Modify: `index.html:100-230`
- Modify: `index.html:1350-1410`
- Modify: `index.html:1510-1560`
- Test: `index.html:1120-1290`

**Interfaces:**
- Produces: `buildMatchGroups(state)`, `ContactMatchCard`, rebuilt `MatchesPage`, expanded `PersonProfilePage`.
- Consumes: `findComplementaryMatches()`, `getUserById()`, `relationshipSummary()`, `locationLabel()`, `formatPublishedAt()`, `ProfileSkillSection`.

- [ ] **Step 1: Add failing contact-group and profile contract tests**

```jsx
test("match directory groups are stable and deduplicated", () => {
  const state=loginAsDemoUser();
  const groups=buildMatchGroups(state);
  assert(groups.complementary.length>=6, "Need six or more complementary contacts");
  assert(groups.favorites.map(user=>user.id).join("|")==="alice-01|jun-01", "Favorites group wrong");
  assert(groups.requests.every(user=>user), "Request target missing");
});

test("every partner supports a complete profile", () => {
  MOCK_USERS.forEach(user=>{
    assert(user.bio.zh&&user.bio.en,`${user.id} missing bilingual bio`);
    assert(user.interests.length>=2,`${user.id} missing interests`);
    assert(user.availability.length>=1,`${user.id} missing availability`);
    assert(user.meetingModes.length>=1,`${user.id} missing meeting mode`);
  });
});
```

- [ ] **Step 2: Run tests and verify `buildMatchGroups` fails**

Expected: new grouping case fails; user data contract remains passing from Task 1.

- [ ] **Step 3: Implement contact groups without duplicate people per group**

```jsx
function uniqueUsers(users){return [...new Map(users.filter(Boolean).map(user=>[user.id,user])).values()];}
function buildMatchGroups(state){
  return {
    complementary:uniqueUsers(findComplementaryMatches(state.currentUser).map(match=>match.user)),
    favorites:uniqueUsers(state.favorites.map(getUserById)),
    requests:uniqueUsers(state.sentRequests.map(request=>getUserById(request.targetUserId)))
  };
}
```

- [ ] **Step 4: Implement the contact card with an explicit details button**

```jsx
function ContactMatchCard({user,currentUser,language,status}){
  const relationship=relationshipSummary(currentUser,user,language);
  return <article className="contact-card glass"><div className="contact-main"><Avatar user={user}/><div><h3>{user.name}</h3><p>{locationLabel(user,language)}</p></div><span className="contact-status">{status||relationship.label}</span></div><div className="contact-skills"><span>{t(language,"canTeach")}: {getSkillName(user.skillsOffered[0].skillId,language)}</span><span>{t(language,"wantsLearn")}: {getSkillName(user.skillsWanted[0].skillId,language)}</span></div><Button small className="contact-details" onClick={()=>navigate(`#/people/${user.id}`,{from:window.location.hash})}>{t(language,"viewFullProfile")} →</Button></article>;
}
```

Render three titled sections—complementary, favorites, requests—using a two-column desktop grid and single-column mobile grid. Show a localized empty state inside a group with no people.

- [ ] **Step 5: Expand `PersonProfilePage` details**

After the existing profile hero and skill columns, render this component and keep Save and Send Request functional:

```jsx
function ProfileDetails({user,language}){
  const facts=[
    [t(language,"languageLabel"),user.languages.map(item=>item.toUpperCase()).join(" / ")],
    [t(language,"availability"),user.availability.map(item=>profileLabel("availability",item,language)).join(" · ")],
    [t(language,"meetingPreference"),user.meetingModes.map(item=>profileLabel("meeting",item,language)).join(" · ")],
    [t(language,"reliability"),`${user.reliability}%`],
    [t(language,"joined"),new Intl.DateTimeFormat(language==="zh"?"zh-CN":"en-US",{year:"numeric",month:"short"}).format(new Date(user.memberSince))]
  ];
  return <section className="profile-details glass"><div><h2>{t(language,"interests")}</h2><div className="skill-pills">{user.interests.map(item=><span className="mini-pill" key={item}>{profileLabel("interests",item,language)}</span>)}</div></div><dl>{facts.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p className="rating-line">♥ {t(language,"likedBy",{count:user.likes})}</p></section>;
}
```

Use semantic sections with headings and lists; do not expose exact address, school, email, or phone. Convert known interest/availability/mode ids through bilingual label maps rather than showing raw ids.

- [ ] **Step 6: Run tests and browser checks**

Expected self-tests: all pass.

Expected browser behavior: Matches is visually distinct from Search, contains more people in compact cards, each card visibly ends with “查看完整资料 →”, profile Back returns to the same Matches Hash, and full profile details render in both languages.

- [ ] **Step 7: Commit Matches and profiles**

```bash
git add index.html
git commit -m "feat: add SkillSwap contact match directory"
```

---

### Task 7: Add Settings routing and eight functional sections

**Files:**
- Modify: `index.html:80-240`
- Modify: `index.html:830-910`
- Modify: `index.html:1310-1350`
- Modify: `index.html:1550-1605`
- Test: `index.html:1120-1290`

**Interfaces:**
- Produces: Settings Hash routes, `updateSettingsState(state,section,value)`, `logoutState(state)`, `SettingsHome`, `SettingsSectionPage`, focused section components.
- Consumes: `normalizeState()`, `SkillEditor`, `ContactMatchCard`, `COUNTRIES`, `getCitiesForCountry()`.

- [ ] **Step 1: Add failing route, settings, and logout tests**

```jsx
test("settings routes parse and old profile route redirects", () => {
  assert(parseHash("#/settings").name==="settings", "Settings home route missing");
  const privacy=parseHash("#/settings/privacy");
  assert(privacy.name==="settings-section"&&privacy.params.section==="privacy", "Settings section route missing");
  assert(parseHash("#/profile").name==="legacy-profile", "Legacy profile route missing");
});

test("logout ends only the session", () => {
  const before=loginAsDemoUser();
  const after=logoutState(before);
  assert(after.isAuthenticated===false&&after.session.loggedIn===false, "Session still active");
  assert(after.currentUser.id===before.currentUser.id, "Logout deleted profile");
  assert(after.favorites.length===before.favorites.length, "Logout deleted favorites");
});

test("settings updates preserve unrelated state", () => {
  const before=loginAsDemoUser();
  const after=updateSettingsState(before,"notifications",{swapRequests:false});
  assert(after.notificationSettings.swapRequests===false, "Notification not updated");
  assert(after.currentUser.id===before.currentUser.id&&after.favorites.length===before.favorites.length, "Unrelated state changed");
});
```

- [ ] **Step 2: Run tests and verify new routes/helpers fail**

Expected: failures name `settings`, `logoutState`, and `updateSettingsState`.

- [ ] **Step 3: Extend router and navigation**

Add exact routes `#/settings` and dynamic `#/settings/<section>`. Supported section ids are `profile`, `skills`, `availability`, `notifications`, `privacy`, `favorites`, and `help`; logout is an action from Settings home, not a page. Parse unknown section ids as `settings-section` and let the page show a recovery notice plus Settings-home button.

Map `#/profile` to `legacy-profile`; in `App` redirect it to `#/settings`. Change desktop and mobile navigation item to `{route:"settings",hash:"#/settings",icon:"⚙",label:t(language,"settings")}` and mark any `settings-section` route active under Settings.

- [ ] **Step 4: Implement state update and logout boundaries**

```jsx
function updateSettingsState(state,section,value){
  if(section==="notifications")return normalizeState({...state,notificationSettings:{...state.notificationSettings,...value}});
  if(section==="privacy")return normalizeState({...state,privacySettings:{...state.privacySettings,...value}});
  if(section==="profile"||section==="skills"||section==="availability")return normalizeState({...state,currentUser:{...state.currentUser,...value}});
  if(section==="feedback")return normalizeState({...state,feedbackEntries:[...state.feedbackEntries,value]});
  return normalizeState(state);
}
function logoutState(state){return normalizeState({...state,isAuthenticated:false,session:{...state.session,loggedIn:false}});}
```

Update route guarding so logged-out users can still reach landing/login/signup while preserved local profile data remains unavailable until login. Demo login restores the Daniel session without needing reset.

- [ ] **Step 5: Build Settings home and functional section pages**

`SettingsHome` displays user summary, public-profile button, and eight rows in the approved order. Each row has a title, short description, icon, and arrow. Logout opens a confirmation; confirmation applies `logoutState`, saves through normal `updateState`, then navigates home.

Implement:

- Profile: avatar control from Task 8, nickname, bilingual bio, country, dependent city.
- Skills: two `SkillEditor` blocks with existing validation.
- Availability: checkbox groups for weekday/weekend time slots, online/public-place modes, and public locations.
- Notifications: three persisted switches.
- Privacy: visibility select and two persisted switches.
- Favorites: `ContactMatchCard` list from current ids.
- Help: fixed bilingual FAQ plus feedback textarea; submit stores `{id,body,createdAt}` locally and shows a success status.

Every form follows this state boundary: `const [draft,setDraft]=React.useState(()=>cloneData(persistedValue));`, Save calls the relevant `updateSettingsState()` branch, and Cancel resets with `setDraft(cloneData(persistedValue))` before navigating to `#/settings`.

- [ ] **Step 6: Rerun tests and exercise every section**

Expected self-tests: all pass.

Browser checks: old `#/profile` redirects, every Settings row opens, Save persists after refresh, Cancel does not persist, Favorites opens complete profiles, Help accepts non-empty feedback, and logout returns home without clearing profile/favorites.

- [ ] **Step 7: Commit Settings routing and forms**

```bash
git add index.html
git commit -m "feat: add complete SkillSwap settings"
```

---

### Task 8: Add safe local avatar processing

**Files:**
- Modify: `index.html:1000-1120`
- Modify: `index.html:1300-1330`
- Modify: `index.html:1550-1605`
- Test: `index.html:1120-1290`

**Interfaces:**
- Produces: `validateAvatarFile(file)`, `resizeAvatar(file,documentRef)`, `AvatarPicker`.
- Consumes: `updateSettingsState()`, `Avatar`.

- [ ] **Step 1: Add failing synchronous validation tests**

```jsx
test("avatar validation accepts only supported images up to 5 MB", () => {
  assert(validateAvatarFile({type:"image/jpeg",size:1024})==="", "JPEG rejected");
  assert(validateAvatarFile({type:"image/png",size:5*1024*1024})==="", "5 MB PNG rejected");
  assert(validateAvatarFile({type:"image/gif",size:1024})==="type", "GIF should fail type");
  assert(validateAvatarFile({type:"image/webp",size:5*1024*1024+1})==="size", "Oversize file should fail");
});
```

- [ ] **Step 2: Run self-tests and verify validation fails**

Expected: `validateAvatarFile is not defined`.

- [ ] **Step 3: Implement validation and center-crop resizing**

```jsx
const AVATAR_TYPES=Object.freeze(["image/jpeg","image/png","image/webp"]);
const AVATAR_MAX_BYTES=5*1024*1024;
function validateAvatarFile(file){
  if(!file||!AVATAR_TYPES.includes(file.type))return "type";
  if(file.size>AVATAR_MAX_BYTES)return "size";
  return "";
}
function resizeAvatar(file,documentRef=document){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onerror=()=>reject(new Error("read"));
    reader.onload=()=>{
      const image=new Image();
      image.onerror=()=>reject(new Error("decode"));
      image.onload=()=>{
        const canvas=documentRef.createElement("canvas"); canvas.width=256; canvas.height=256;
        const context=canvas.getContext("2d"); const side=Math.min(image.width,image.height);
        context.drawImage(image,(image.width-side)/2,(image.height-side)/2,side,side,0,0,256,256);
        resolve(canvas.toDataURL("image/jpeg",.86));
      };
      image.src=reader.result;
    };
    reader.readAsDataURL(file);
  });
}
```

- [ ] **Step 4: Build `AvatarPicker` with preview, error, cancel, and save behavior**

Keep `pendingDataUrl` local. On file choice: validate, show localized type/size error, then resize; processing failure keeps the previous image. Show preview through the existing `Avatar` component by passing a draft user with `avatarDataUrl`. Save writes only the resized Data URL. Cancel clears the file input and draft without updating state.

Update `Avatar` source precedence to `user.avatarDataUrl || user.avatar`.

- [ ] **Step 5: Run tests and manually verify image paths**

Expected self-tests: all pass.

Use one valid JPEG under 5 MB, one GIF, and one image over 5 MB. Expected: JPEG previews and survives refresh after Save; GIF and oversized files show localized errors; Cancel leaves the old avatar; a decoding failure does not blank the old avatar.

- [ ] **Step 6: Commit avatar support**

```bash
git add index.html
git commit -m "feat: support local profile avatars"
```

---

### Task 9: Complete regression, visual QA, documentation, GitHub publication

**Files:**
- Modify: `index.html`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-16-skillswap-first-round-improvements.md`
- Create: `.nojekyll` — publish the static repository directly without parsing implementation-plan examples as Liquid templates.

**Interfaces:**
- Consumes: all components and helpers from Tasks 1-8.
- Produces: verified local build, checked-off plan, updated README, GitHub commit(s), built GitHub Pages deployment.

- [x] **Step 1: Run the full inline self-test suite**

Open `http://localhost:4173/?selftest=1`.

Expected: zero failures; the report count is higher than the previous 24 and includes every test named in Tasks 1-8.

- [x] **Step 2: Run the complete desktop story**

At 1440 px width:

1. Reset demo and confirm the landing hero has no duplicate action row.
2. Confirm tag palette/motion and three community numbers.
3. Log in through the retained header route and enter Daniel demo.
4. Click a trending skill into Search.
5. Reset Search; confirm the starter content appears without people.
6. Select China, Tianjin, then switch to Japan; confirm city clears.
7. Compare newest and most-liked ordering.
8. Open a result profile and return with filters intact.
9. Open Matches; inspect all three contact groups and every details button.
10. Open a full profile and verify interests, availability, ratings, reliability, and match reasons.
11. Open all seven Settings sections, change notification `swapRequests`, save, refresh, and verify the switch remains changed.
12. Log out and verify profile data remains after logging back into the demo.

Expected: no console errors, broken navigation, horizontal overflow, dead buttons, or inconsistent card footers.

- [x] **Step 3: Run mobile and accessibility checks**

At 390 × 844:

- Confirm bottom navigation labels Discover, Search, Matches, Settings.
- Confirm Search filters stack without clipping and city disabling is understandable.
- Confirm contact cards are one column and details buttons are visible.
- Confirm Settings rows and forms have 44 px touch targets.
- Enable reduced motion and confirm marquees stop.
- Tab through forms and dialogs; focus remains visible and modal Escape/focus restoration still work.

Expected: no horizontal scroll and no action available only on hover.

- [x] **Step 4: Update README with the implemented behavior**

Replace outdated bullets that advertise SkillLoop and Profile navigation. Document the rose marquee, Search country/city workflow, contact-style Matches, complete Settings, fixed demo community/trending numbers, local avatar limit, self-test URL, and the existing GitHub Pages URL.

- [x] **Step 5: Run repository verification**

```bash
git diff --check
git status --short --branch
```

Expected: `git diff --check` prints nothing. Status contains only the intended `index.html`, `README.md`, and plan checkbox changes; `.DS_Store` and `.superpowers/` remain untracked and unstaged.

- [x] **Step 6: Commit the verified release**

```bash
git add index.html README.md docs/superpowers/plans/2026-08-16-skillswap-first-round-improvements.md
git commit -m "feat: deliver SkillSwap first-round improvements"
```

- [x] **Step 7: Publish and verify GitHub Pages**

Push or publish the new commit to `LeonLiu09/MicroEngine-SkillSwap20` on `main`. Confirm the remote `main` SHA, confirm README contains the live URL, poll the Pages API until `status` is `built`, then open `https://leonliu09.github.io/MicroEngine-SkillSwap20/` and repeat the landing plus Daniel-login smoke path online.

Expected: HTTPS is enforced, source remains `main /`, the online page matches the local release, and the final deliverable tab is the public GitHub Pages URL.

Verified 2026-08-17: remote `main` was published without a force update. The first Pages run exposed a Jekyll/Liquid conflict in this plan document; adding the official static-site `.nojekyll` marker resolved it. Pages run `31993355380` completed successfully, the public URL returned HTTP 200, and its `index.html` blob SHA `d34263d7cdf575dfca149b192a0d3d96558ef24b` exactly matched the 47/47-tested local release.
