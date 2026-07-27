# SQL to ER Diagram — Kiro CLI Agent

A Kiro CLI agent that generates Mermaid ER diagrams from SQL schema files. Based on the [aws-samples repo](https://github.com/aws-samples/sample-to-create-mermaid-entity-diagrams-from-sql-using-agentic-ai-on-agentcore) which uses Amazon Bedrock AgentCore — adapted here to run locally as a prompt-driven Kiro CLI agent.

## Prerequisites

- [Kiro CLI](https://kiro.dev) installed and configured
- Terminal access

## Setup

Open a terminal and run the following commands:

**Step 1:** Create the required Kiro directories (if they don't already exist):

```bash
mkdir -p ~/.kiro/agents/
mkdir -p ~/.kiro/steering/
```

**Step 2:** Copy the agent config (defines the agent name, description, and routing):

```bash
cp sql-er-diagram.json ~/.kiro/agents/
```

**Step 3:** Copy the steering file (provides the agent's instructions and behavior):

```bash
cp steering/sql-er-diagram.md ~/.kiro/steering/
```

> **Note:** Both files live in your home directory under `~/.kiro/`. This means the agent is available globally — you do NOT need to open any specific project in Kiro IDE.

**Step 4:** Start a new Kiro CLI chat session from your home directory:

```bash
cd ~
kiro-cli chat
```

**Step 5:** Inside the Kiro chat, invoke the agent:

```
/agent sql-er-diagram
```

You're now ready to generate ER diagrams.

## Usage

Once the agent is active, ask it to generate a diagram:

```
Generate an ER diagram from samples/ecommerce.sql
```

Or paste SQL inline:

```
Generate an ER diagram from this:
CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(100));
CREATE TABLE orders (id INT PRIMARY KEY, user_id INT, FOREIGN KEY (user_id) REFERENCES users(id));
```

The agent will:
1. Parse the SQL and identify tables, columns, and relationships
2. Generate a Mermaid ER diagram
3. Ask where to save the `.mmd` file (defaults to `~/`)

## Using with Kiro IDE

The same setup works with Kiro IDE — no project upload needed. Since the agent config and steering file are in `~/.kiro/` (global), simply:

1. Open Kiro IDE
2. Use `/agent sql-er-diagram`
3. Ask it to generate a diagram

## Project Structure

```
kiro-cli/sql-agent/
├── sql-er-diagram.json         # Agent config → copy to ~/.kiro/agents/
├── steering/
│   └── sql-er-diagram.md       # Steering file → copy to ~/.kiro/steering/
├── README.md
└── samples/
    ├── ecommerce.sql           # 5-table e-commerce schema
    └── hr-management.sql       # 5-table HR schema with self-referencing FK
```

## What Gets Installed Where

| File | Destination | Purpose |
|------|-------------|---------|
| `sql-er-diagram.json` | `~/.kiro/agents/` | Registers the agent (name, description, routing) |
| `steering/sql-er-diagram.md` | `~/.kiro/steering/` | Agent instructions (how to parse SQL and generate Mermaid) |

## Sample Files

- `samples/ecommerce.sql` — customers, products, orders, order_items, reviews
- `samples/hr-management.sql` — departments, employees, projects, project_assignments, timesheets (includes self-referencing FK on employees.manager_id)

## Visualizing Output

Paste the generated Mermaid diagram into [mermaid.live/edit](https://mermaid.live/edit) to render it.

## Uninstall

```bash
rm ~/.kiro/agents/sql-er-diagram.json
rm ~/.kiro/steering/sql-er-diagram.md
```
