"""The 40-query test set and the original 20 (text ones). (id, category, query, need)."""
Q40 = [
    # known-item
    ("k1", "known-item", "lethal trifecta simonw", "The post by Simon Willison (@simonw) about the 'lethal trifecta' risk for AI agents that call tools."),
    ("k2", "known-item", "levelsio hetzner tailscale", "The post by @levelsio about what he does first when setting up a new Hetzner VPS (installing Tailscale, locking it down)."),
    ("k3", "known-item", "markdown.new clean markdown from any URL", "The post announcing markdown.new: put it before any URL to get clean Markdown back."),
    ("k4", "known-item", "that open source LLM eval framework Hamel liked", "The post where Hamel Husain says he found an open-source, vendor-neutral LLM eval framework he likes (Inspect)."),
    ("k5", "known-item", "napkin math turbopuffer", "The post about using napkin math while building turbopuffer."),
    ("k6", "known-item", "difference between JSON mode and function calling", "A post explaining the difference between OpenAI JSON mode and function calling."),
    # topical
    ("t1", "topical", "RAG evaluation metrics", "Posts about how to evaluate RAG systems: metrics, frameworks, benchmarks for retrieval and answer quality."),
    ("t2", "topical", "fine-tuning open source LLMs with LoRA", "Posts about fine-tuning open-source LLMs, especially with LoRA / QLoRA / PEFT: guides, tools, notebooks."),
    ("t3", "topical", "football computer vision player tracking", "Posts about computer vision for football/soccer (or similar team sports): detecting and tracking players and the ball."),
    ("t4", "topical", "Claude Code skills and subagents", "Posts about Claude Code skills, slash commands, or subagents: how to write or use them, examples."),
    ("t5", "topical", "three.js games made with AI", "Posts about making games or 3D experiences with three.js using AI coding (vibe coding)."),
    ("t6", "topical", "text to SQL", "Posts about text-to-SQL: turning natural-language questions into SQL with LLMs, tools, papers, benchmarks."),
    # project relevance
    ("p1", "project", "everything useful for building a Chrome extension that captures network responses", "Material useful to someone building a Chrome/browser extension that intercepts or captures network responses: extension how-tos, browser automation/interception techniques, scraping via network requests, relevant tools."),
    ("p2", "project", "what should I read before building hybrid search on Postgres", "Material useful before building hybrid (keyword + vector) search on Postgres: BM25 vs embeddings, pgvector, rank fusion, reranking, search quality in Postgres."),
    ("p3", "project", "how to deploy a side project on a cheap VPS securely", "Material useful for deploying a side project on a cheap VPS: server setup, hardening/security, self-hosting, deployment tools, Hetzner and similar."),
    ("p4", "project", "resources for building a real-time voice agent", "Material useful for building a real-time voice AI agent: speech-to-speech, speech-to-text, text-to-speech models, voice agent frameworks, latency."),
    ("p5", "project", "tools for parsing PDFs and tables for RAG", "Tools and techniques for parsing PDFs, tables and complex documents for RAG pipelines."),
    ("p6", "project", "building a personal knowledge base or second brain with AI", "Material useful for building a personal knowledge base / second brain with AI: note-taking with LLMs, bookmark or memory organisers, personal RAG over notes."),
    # typo / partial
    ("y1", "typo", "retriev", "Posts about retrieval (retrieval-augmented generation, retrievers, information retrieval)."),
    ("y2", "typo", "karpthy neural networks", "Posts by or about Andrej Karpathy on neural networks (lectures, videos, explanations)."),
    ("y3", "typo", "langchian agents", "Posts about building agents with LangChain (or LangGraph)."),
    ("y4", "typo", "embedings", "Posts about embeddings: embedding models, how to use or train them, vector representations for search."),
    ("y5", "typo", "hugingface course", "Posts about courses from Hugging Face (free courses, tutorials by Hugging Face)."),
    # short two-word
    ("s1", "short", "prompt caching", "Posts about prompt caching (caching LLM prompts/context to cut cost and latency)."),
    ("s2", "short", "vector database", "Posts about vector databases: products, comparisons, how they work, when to use them."),
    ("s3", "short", "MCP server", "Posts about MCP (Model Context Protocol) servers: building, using or listing them."),
    ("s4", "short", "landing page", "Posts about making landing pages: design tips, tools, templates, AI generators for landing pages."),
    ("s5", "short", "knowledge graph", "Posts about knowledge graphs, including GraphRAG and building graphs with LLMs."),
    # long natural-language questions
    ("l1", "long", "which bookmarks explain how mixture of experts models work?", "Posts that explain how Mixture of Experts (MoE) models work."),
    ("l2", "long", "what are the best practices for chunking documents for retrieval augmented generation?", "Posts about chunking strategies / best practices for splitting documents in RAG."),
    ("l3", "long", "how do people find startup ideas using Reddit and AI?", "Posts about finding or validating startup/business ideas using Reddit (possibly with AI tools)."),
    ("l4", "long", "how can I reduce the cost of LLM API calls as token usage grows?", "Posts about reducing LLM API/token cost: caching, routing, cheaper models, spend control."),
    ("l5", "long", "is fine-tuning better than RAG for domain specific knowledge?", "Posts that compare or take a position on fine-tuning versus RAG for giving a model domain knowledge."),
    # qualifier + text
    ("q1", "qualifier", "from:omarsar0 RAG paper", "Posts by @omarsar0 presenting a paper about RAG."),
    ("q2", "qualifier", "domain:github.com agent framework", "Posts linking to a GitHub repository of an AI agent framework or library for building agents."),
    ("q3", "qualifier", "type:video robotics", "Video posts about robots or robotics."),
    ("q4", "qualifier", "domain:arxiv.org posted:2024 agents", "Posts from 2024 linking to an arXiv paper about LLM/AI agents."),
    ("q5", "qualifier", "from:HamelHusain evals", "Posts by Hamel Husain about LLM evals / evaluation."),
    # negatives: nothing in the library should really answer these
    ("n1", "negative", "kubernetes operator for cassandra", "Posts about a Kubernetes operator for Apache Cassandra."),
    ("n2", "negative", "sourdough bread recipe", "A recipe or guide for baking sourdough bread."),
    ("n3", "negative", "rust borrow checker lifetimes", "Posts explaining Rust's borrow checker or lifetimes."),
    ("n4", "negative", "terraform aws vpc peering", "Posts about configuring AWS VPC peering with Terraform."),
]

# The text queries of the first evaluation (search_eval_2026-11-21.md), same strings.
Q20 = [
    ("Q1", "orig", "pgvector HNSW tuning is:thread", "A thread about tuning pgvector HNSW indexes."),
    ("Q2", "orig", "from:simonw SQLite full-text search embeddings", "A post by @simonw comparing SQLite full-text search to embeddings (or search-based RAG with SQLite/Datasette)."),
    ("Q3", "orig", "type:video agent booking a flight demo", "A video demo of an AI agent booking a flight (or travel)."),
    ("Q4", "orig", "domain:github.com Postgres job queue", "A post linking to a GitHub repo for a Postgres-based job queue."),
    ("Q5", "orig", "from:karpathy evals", "Posts by @karpathy about evals / evaluation benchmarks."),
    ("Q6", "orig", "type:video duration:<5m prompt caching", "Short videos about prompt caching."),
    ("Q7", "orig", "domain:github.com RAG", "Posts linking to GitHub repositories for RAG."),
    ("Q8", "orig", "type:article lang:en posted:2026 len:>3000 MCP server authentication", "Long English articles from 2026 about MCP server authentication."),
    ("Q9", "orig", "Chrome extension that intercepts fetch requests", "Material useful for designing a Chrome extension that intercepts fetch requests."),
    ("Q10", "orig", "building hybrid search on Postgres", "What to read before building hybrid search on Postgres."),
    ("Q11", "orig", "job queue Redis alternative for a small server", "Tools that could replace Redis as a job queue on a small server."),
    ("Q14", "orig", "fine-tuning versus RAG", "Posts that take a side on whether fine-tuning beats RAG."),
]

# Added when the LinkedIn saves arrived (833 items, source `linkedin`). Run on the whole library.
Q_LI = [
    ("i1", "linkedin", "Sebastian Raschka PyTorch beginner material", "The LinkedIn post by Sebastian Raschka about beginner-friendly PyTorch material for people getting into LLMs."),
    ("i2", "linkedin", "source:linkedin techniques to improve RAG accuracy", "LinkedIn posts about techniques that improve RAG pipeline accuracy."),
    ("i3", "linkedin", "system design interview resources", "Posts collecting system design interview resources (guides, compilations, repositories)."),
    ("i4", "linkedin", "source:linkedin fine-tuning notebook", "LinkedIn posts sharing a notebook or walkthrough for fine-tuning a model."),
]
