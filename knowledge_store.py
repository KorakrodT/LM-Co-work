import json
import os
import re
from typing import List, Dict

# A very simple, dependency-free TF-IDF and Cosine Similarity implementation for local RAG
class SimpleVectorStore:
    def __init__(self, filename: str = ".knowledge_base.json"):
        self.filename = filename
        self.documents: List[Dict[str, str]] = []  # {"id": str, "content": str, "metadata": dict}
        self._last_data_file = None

    @property
    def data_file(self):
        import tools
        return os.path.join(tools.WORKSPACE, self.filename)

    def _ensure_loaded(self):
        df = self.data_file
        if self._last_data_file != df:
            self._last_data_file = df
            self.load()

    def load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.documents = json.load(f)
            except Exception:
                self.documents = []
        else:
            self.documents = []

    def save(self):
        tmp = self.data_file + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.documents, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.data_file)

    def add_document(self, doc_id: str, content: str, metadata: dict = None):
        self._ensure_loaded()
        # Remove existing if any
        self.documents = [d for d in self.documents if d["id"] != doc_id]
        self.documents.append({"id": doc_id, "content": content, "metadata": metadata or {}})
        self.save()
        
    def _tokenize(self, text: str) -> set:
        # Simple word tokenizer, converting to lowercase
        words = re.findall(r'\w+', text.lower())
        return set(words)

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        self._ensure_loaded()
        if not self.documents:
            return []
            
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
            
        scores = []
        for doc in self.documents:
            doc_tokens = self._tokenize(doc["content"])
            if not doc_tokens:
                scores.append(0.0)
                continue
                
            # Jaccard similarity as a simple proxy for TF-IDF/Cosine
            intersection = query_tokens.intersection(doc_tokens)
            union = query_tokens.union(doc_tokens)
            score = len(intersection) / len(union) if union else 0.0
            scores.append(score)
            
        # Rank documents
        ranked = sorted(zip(self.documents, scores), key=lambda x: x[1], reverse=True)
        # Filter out zero scores and return top_k
        results = [doc for doc, score in ranked if score > 0][:top_k]
        return results

store = SimpleVectorStore()

def add_to_knowledge(doc_id: str, content: str, metadata: dict = None) -> str:
    store.add_document(doc_id, content, metadata)
    return f"Document '{doc_id}' added to knowledge base."

def search_knowledge(query: str, top_k: int = 3) -> str:
    results = store.search(query, top_k)
    if not results:
        return "No relevant information found in the knowledge base."
    
    out = ["Found relevant knowledge:"]
    for r in results:
        out.append(f"\n--- Document: {r['id']} ---\n{r['content']}\n")
    return "\n".join(out)
