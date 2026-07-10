# Modern Developer SaaS UI Design System

## Overview

**Style Name:** Modern Dark Developer SaaS

A premium enterprise UI designed for developer tools and internal platforms. The interface should prioritize readability, strong information hierarchy, generous whitespace, subtle depth, and restrained use of color. The overall experience should feel polished, calm, and efficient rather than flashy.

---

# Visual Inspiration

Use these products as inspiration:

- Linear
- Vercel Dashboard
- GitHub
- Railway
- Supabase
- Clerk
- PlanetScale
- Stripe Dashboard
- Arc Browser
- Raycast

Avoid taking inspiration from:

- Bootstrap admin templates
- Material Design 2
- Windows Forms
- Atlassian Jira
- Generic CRUD dashboards
- Bright, colorful admin themes

---

# Core Design Principles

## 1. Strong Information Hierarchy

The interface should naturally guide the user's eye.

```
Project
    ↓
Status
    ↓
Resources
    ↓
Actions
```

Names should always be more prominent than IDs.

Metadata should never compete with primary information.

---

## 2. Progressive Disclosure

Don't overwhelm users with controls.

Avoid:

```
Edit
Delete
Remove
Settings
```

Instead:

```
⋮
```

Open contextual menus only when needed.

Forms should stay collapsed until requested.

---

## 3. Cards Instead of Tables

Represent important resources as cards instead of rows whenever possible.

Instead of

```
frontend
backend
shared
```

Use individual cards.

Cards communicate ownership and grouping far better than tables.

---

## 4. Minimal Color Usage

90% of the interface should be grayscale.

Use orange only for:

- Primary buttons
- Active selections
- Focus states
- Links
- Progress

Use red only for:

- Delete
- Remove
- Dangerous actions

Avoid multiple competing accent colors.

---

## 5. Soft Depth

Create hierarchy using subtle elevation.

Use:

- slightly lighter surfaces
- thin borders
- soft shadows

Avoid:

- harsh borders
- heavy shadows
- neumorphism

---

# Color Palette

## Background

```
#181A1F
```

## Primary Surface

```
#23262D
```

## Elevated Surface

```
#2B3038
```

## Input Background

```
#303640
```

## Border

```
#404754
```

## Primary Accent

```
#FF8A1E
```

## Primary Hover

```
#FFA33A
```

## Success

```
#4ADE80
```

## Warning

```
#FACC15
```

## Danger

```
#E5484D
```

## Primary Text

```
#FFFFFF
```

## Secondary Text

```
#A7ADB8
```

## Muted Text

```
#707784
```

---

# Typography

Preferred fonts:

- Inter
- Geist

Weights:

| Usage | Weight |
|--------|---------|
| Page Title | 700 |
| Section Header | 600 |
| Card Title | 600 |
| Labels | 500 |
| Body | 400 |
| Secondary Text | 400 |

Never overuse bold text.

Rely on whitespace more than typography.

---

# Corner Radius

| Component | Radius |
|-----------|---------|
| Main Cards | 16px |
| Nested Cards | 12px |
| Inputs | 10px |
| Buttons | 10px |
| Badges | 999px |

Maintain consistency.

---

# Shadows

Use subtle elevation.

```
0 4px 18px rgba(0,0,0,.22)
```

Cards should appear to float slightly above the background.

---

# Spacing System

Use an 8pt spacing scale.

```
4
8
12
16
24
32
40
48
64
```

Never use arbitrary spacing values.

---

# Layout Philosophy

Every page should follow this structure:

```
Page Header

↓

Status Summary

↓

Primary Content

↓

Secondary Actions

↓

Danger Zone
```

Never mix destructive actions with normal workflows.

---

# Cards

Cards should have:

- generous padding
- rounded corners
- soft shadows
- subtle borders
- clear titles

Example:

```
┌───────────────────────────────┐

Frontend

GitHub Repository
issachar-vin/ChessLearnerUI

Base Branch
main

                         ⋮

└───────────────────────────────┘
```

Hover State:

- slightly lighter background
- subtle shadow increase
- orange border

---

# Buttons

## Primary

- Orange background
- White text

Example:

```
+ Add Repository
```

---

## Secondary

Dark outlined button.

Example:

```
Import from Plane
```

---

## Ghost

Transparent background.

Used for icon buttons.

---

## Danger

Dark background.

Red text.

Example:

```
Delete Project
```

Danger buttons should never be the primary visual focus.

---

# Inputs

Inputs should be:

- 48–52px tall
- full width when appropriate
- clearly labeled
- include helper text

Example:

```
Repository Identifier

[____________________]

Unique name used internally.

Examples:
frontend
backend
docs
shared-ui
```

---

# Repository Cards

Each repository should feel like a resource.

Example:

```
Frontend

GitHub
issachar-vin/ChessLearnerUI

Branch
main

                    ⋮
```

Instead of giant Remove buttons, use a contextual menu.

Menu:

```
Edit Repository

Change Branch

Remove Repository
```

---

# Status Indicators

Use small colored indicators instead of colored text.

Examples:

```
● Secret Connected

● 2 Repositories

● Sync Enabled

● Last Sync 3 minutes ago
```

---

# Badges

Use pill-shaped badges.

Examples:

```
FRONTEND

BACKEND

PRODUCTION

MAIN

DEVELOP
```

Badges should be:

- muted
- compact
- uppercase

---

# Icons

Preferred icon library:

- Lucide

Recommended icons:

- Github
- Folder
- Package
- Monitor
- Server
- Database
- Lock
- Key
- Git Branch
- Rocket
- Shield
- Settings

Icons should use 1.5–2px strokes.

---

# Motion

Animations should be subtle.

Duration:

```
150ms
```

Timing:

```
ease-out
```

Hover:

- translateY(-2px)
- shadow slightly increases
- border changes to orange

Menus:

- fade
- slight scale
- no bouncing

---

# Forms

Forms should be hidden until needed.

Instead of permanently displaying:

```
Repository Identifier

Repository

Branch

Add
```

Use:

```
Repositories

+ Add Repository
```

Expands into:

```
Repository Type

Repository Identifier

GitHub Repository

Base Branch

Cancel

Add Repository
```

This keeps the interface clean.

---

# Information Hierarchy

Always prioritize:

1. Project Name
2. Current Status
3. Resources
4. Metadata
5. Actions
6. Dangerous Actions

Example:

```
Chess Learner

Secret Connected

Repositories

Frontend

Backend

Import

Delete Project
```

Not:

```
UUID

Buttons

Random Metadata

Project Name
```

---

# UX Best Practices

- Prefer whitespace over borders.
- Never let destructive actions dominate the screen.
- Keep controls close to the content they affect.
- Make common actions obvious.
- Hide advanced actions behind contextual menus.
- Use consistent spacing throughout the application.
- Keep typography clean and restrained.
- Avoid visual noise.
- Every card should feel like an object.
- Every page should have one obvious primary action.

---

# Overall Feeling

The application should feel:

- Premium
- Professional
- Modern
- Calm
- Fast
- Minimal
- Developer-focused
- Enterprise-ready
- Consistent
- Intentional

Users should immediately think of products like:

- Linear
- Vercel
- Railway
- Supabase
- GitHub
- Stripe Dashboard

The interface should never resemble a generic admin template.

---

# Master Prompt

> Design every interface using a **Modern Dark Developer SaaS** design language inspired by **Linear, Vercel, GitHub, Railway, Supabase, Stripe, Clerk, and PlanetScale**. Use **card-based layouts**, **soft elevation**, **rounded corners (10–16px)**, **generous whitespace**, **an 8pt spacing system**, **Inter or Geist typography**, **Lucide icons**, and **a restrained grayscale palette with a single orange accent color**. Prioritize **clear information hierarchy**, **progressive disclosure**, and **contextual actions** over cluttered interfaces. Represent resources as cards instead of tables, keep destructive actions isolated in a danger zone, and ensure every screen has one clear primary action. The UI should feel premium, developer-centric, calm, and production-ready rather than like a generic CRUD admin dashboard.