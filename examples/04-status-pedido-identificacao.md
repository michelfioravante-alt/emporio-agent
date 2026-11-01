# Order status with identity verification

> For reviewers: this is a real conversation with the agent (`gemini-3.5-flash-lite`), recorded on Sunday, 2026-11-01 at 18:17 (Campo Grande time). The store was closed; the next opening was Monday at 09:00. The dialogue stays in Portuguese, the language the agent actually used. The customer asks for an order status. The agent refuses to reveal it until the order number matches the full name or the phone number, so one customer cannot read another customer's order.

**Cliente:** Qual o status do pedido 8?

_Ferramentas: nenhuma_

**Agente:**

> Para consultar o pedido 8, preciso do seu **nome completo** ou do seu **telefone com DDD** usado na compra. Pode me passar?

---

**Cliente:** Sou a Ana.

_Ferramentas: `get_order_status`_

**Agente:**

> Não encontrei um pedido com o número 8 e o nome Ana. Por favor, confira o número do pedido e o nome completo ou o telefone com DDD usado na compra para eu tentar de novo.

---

**Cliente:** Ana Carolina Ferreira.

_Ferramentas: `get_order_status`_

**Agente:**

> Olá, Ana! 
>
> O seu pedido 8 está com o status **enviado (em transporte)**. A previsão de entrega é para o dia 28/02/2026 e o código de rastreio é **BRJL5544332BR**.
>
> Posso ajudar com mais alguma coisa?
