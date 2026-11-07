# Local development setup for the Harbor monorepo

This guide takes a new engineer from a fresh laptop to a running Harbor stack. The monorepo holds the web app, the API services, the shared component library and the mobile apps.

## Requirements

You need Node.js 22, pnpm 9, Docker Desktop and Python 3.12 for the data scripts. macOS and Linux are supported directly; on Windows, use WSL 2 and keep the repository inside the Linux file system, because builds are about four times slower on the mounted Windows drive.

## Install

Clone the repository and run `pnpm install` at the root. The workspace links every package, so a change in the component library shows up in the web app without publishing. The first install downloads about 1.8 GB and takes around six minutes.

## Services

`docker compose up` starts Postgres 16, Redis 7 and a local mail catcher. The API services run outside Docker with `pnpm dev:api` so that file changes reload in under a second. Seed data comes from `pnpm db:seed`, which creates three demo accounts, one of them with 50,000 invoices for testing pagination.

## Environment

Copy `.env.example` to `.env.local`. Never commit `.env.local`; the pre commit hook refuses it. Secrets for third party sandboxes come from the team's password manager, not from chat.

## Running the web app

`pnpm dev:web` starts the web app on port 5173 against the local API. The design token build runs in watch mode alongside it, so editing the token file updates the CSS variables immediately.

## Common problems

If `pnpm install` fails on native modules, remove `node_modules` and the pnpm store cache, then install again. If the API cannot reach Postgres on Windows, check that the WSL 2 network mode is mirrored. If seed data looks old, run `pnpm db:reset`, which drops and recreates the local database.

## Before your first pull request

Read the frontend code review guidelines and run `pnpm check`, which runs lint, type checks and unit tests the same way CI does.
