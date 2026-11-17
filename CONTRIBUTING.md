# Contribuindo / Contributing

Obrigado por querer ajudar! / Thanks for helping out. Issues and pull requests can be written in Portuguese or English.

## O que mais ajuda / What helps most

O projeto foi testado **apenas numa Sony Bravia KDL-42W655A**. A contribuição mais valiosa é dizer o que funciona (ou não) no seu modelo: códigos de teclas, comportamento de ligar/desligar, busca da TV na rede. Para relatar um modelo, abra uma issue com **"Relatar modelo de TV"**.

## Rodando o projeto

Requer Python 3.8+ (só biblioteca padrão).

```bash
python server.py            # acessível pelo celular na mesma rede Wi-Fi
python server.py --local    # somente neste computador
```

Para testar você precisa de uma TV Sony Bravia na mesma rede, com **Controle IP** ativado (Configurações > Rede > Controle IP, autenticação "Normal e Pre-Shared Key").

## Pull requests

1. Faça um fork e crie uma branch a partir da `main`.
2. Mantenha a mudança pequena e focada em um assunto.
3. Código, nomes e comentários em inglês; textos da interface em português.
4. Diga no pull request **em qual modelo de TV você testou** e o que viu acontecer.
5. Não inclua dados pessoais (IP, MAC, chaves) em código, capturas de tela ou logs.
6. Biblioteca padrão apenas: o servidor não deve exigir `pip install`.

O servidor não tem autenticação e é pensado para a rede doméstica. Mudanças que o exponham à internet precisam vir com autenticação.
