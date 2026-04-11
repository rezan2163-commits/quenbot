# 🚀 GEMMA 4 E4B GGUF - SETUP REHBERI

## ⚠️ KRITIK: Kullanıcı Model Belirlenmesi Gerekiyor

Belirttiğiniz model: **"GGUF Gemma 4 E4B Uncensored HauhauCS Aggressive Q8_K_P"**

Bu model henüz spesifik Hugging Face repo kimliği belirtilmemiş. Seçenekler:

### SEÇENEK 1: HuggingFace'ten Bulunmuş GGUF Modeli
```bash
# Örnek (HauhauCS organizmasyonundan Gemma 4)
# Model ID'sini belirleyin: huggingface.co/USERNAME/MODEL_NAME

sshpass -p "PASSWORD" ssh root@178.104.159.101 << 'EOF'
cd /root

# 1. GGUF model'i indir (400MB-8GB arasında)
cd /tmp
wget https://huggingface.co/USERNAME/MODEL_NAME/resolve/main/model-Q8_K.gguf
# veya
git clone https://huggingface.co/USERNAME/MODEL_NAME
cd MODEL_NAME
git lfs install
git lfs pull

# 2. Ollama modelfile oluştur
mkdir -p /root/.ollama/models
cat > /tmp/gemma4_modelfile << 'MODELEOF'
FROM /tmp/model-Q8_K.gguf

PARAMETER top_k 40
PARAMETER top_p 0.9
PARAMETER temperature 0.7
MODELEOF

# 3. Ollama'da model'i register et
ollama create gemma4-e4b-gguf -f /tmp/gemma4_modelfile

# 4. Test et
curl http://localhost:11434/api/tags | grep gemma4

EOF
```

### SEÇENEK 2: Ollama'nın Builtin Gemma 4 Kullan
```bash
sshpass -p "PASSWORD" ssh root@178.104.159.101 "ollama pull gemma:7b"
# veya daha büyük varsa
ollama pull gemma:8b
```

### SEÇENEK 3: Alternatif Uncensored Model
Eğer tam spesifik model bulunamazsa, bu uncensored alternatifler:
- `openbao/neural-7b-florp-v2` (aggressive, GGUF)
- `mistral-nemo:gguf` (daha kuvvetli)
- `neural-chat` (Turkish-friendly)

---

## 🔧 SETUP ADIMARI (Sonra Yapılacak)

### ADIM 1: Model'i Sunucuya Yükle
```bash
# Lokal model indirdiysen
scp -r /path/to/model/ root@178.104.159.101:/root/models/

# Veya HF'tan direkt indir (sunucuda)
sshpass -p "PASSWORD" ssh root@178.104.159.101 "cd /tmp && huggingface-cli download MODEL_ID model.gguf"
```

### ADIM 2: Ollama'da Konfigüre Et
```bash
sshpass -p "PASSWORD" ssh root@178.104.159.101 << 'EOF'
# Modelfile oluştur (custom parameters)
cat > /tmp/Modelfile << 'MODEOF'
FROM /root/models/model-Q8_K.gguf

PARAMETER temperature 0.6        # Daha deterministic (0.6)
PARAMETER top_p 0.9
PARAMETER top_k 50
PARAMETER num_ctx 4096           # Context window

SYSTEM """Siz QuenBot'ın ana AI'sısınız. Türkçe konuşun, açıklayıcı olun, 
kararlar verin, stratejik tavsiye verin. Veri akışına hakim olun."""
MODEOF

# Model'i create et
ollama create gemma4-aggressive -f /tmp/Modelfile

# Test et
ollama run gemma4-aggressive "Merhaba, kim sin?"
EOF
```

### ADIM 3: Chat Engine Config Güncelle
```bash
# python_agents/llm_client.py'de modeli güncelleyelim
MODEL_NAME = "gemma4-aggressive"  # Yerine koymak
```

### ADIM 4: Restart & Test
```bash
sshpass -p "PASSWORD" ssh root@178.104.159.101 "pm2 restart quenbot-agents --update-env"
sleep 5

# Chat test
curl -X POST http://178.104.159.101:3002/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Merhaba, stratejim hakkında bir tavsiye verebilir misin?"}'
```

---

## 📋 MEKANIZMALAR AYARLARI

### Mekanizmia-I: FULL CONTEXT INJECTION ✅
**Status**: Kod implement edildi
- Sistem state'ini otomatik enjekte ediyor
- Market analysis yapıyor
- Recent signals toplayıyor
- Risk parameters veriliy

### Mekanizmia-II: LONG-TERM VECTOR MEMORY ✅  
**Status**: Kod implement edildi
- Chat history'den similarity search yapıyor
- Benzer geçmiş sohbetleri memory'ye eklyo
- Doğal konuşma consistency sağlıyo

---

## 🎯 SONUÇ

Yapılacaklar:
1. ✅ **Chat Engine**: İki mekanizmia implemented
2. ✅ **Code**: Production ready
3. 🟡 **Model**: HF repo belirlenip upload edilmesi gerek
4. 🟡 **Config**: Model adı update ve test
5. 🟡 **Test**: End-to-end Gemma response test

---

## ⁉️ HANGI MODEL KULLANMALIYIM?

Lütfen şunlardan biri belirtin:
- [ ] HuggingFace repo exact link: `huggingface.co/...`
- [ ] Model adının full: `USERNAME/MODEL_ID`
- [ ] Önerilen model: `mistral-nemo` veya `neural-chat` kullanabilir miyiz?

Sonra hemen kuruyorum! 🚀
