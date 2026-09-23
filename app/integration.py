"""Verified public contact channels and explicit platform integration boundaries."""
from fastapi import Query

CONTACTS = {
    'phone': '+7 (727) 346-88-88', 'phone_url': 'tel:+77273468888',
    'mobile': '+7 (778) 046-88-88', 'mobile_url': 'tel:+77780468888',
    'whatsapp_url': 'https://wa.me/77782768888/',
    'email': 'almaty@ekt.kz', 'email_url': 'mailto:almaty@ekt.kz',
    'source': 'https://ekt.kz/about/contacts/', 'verified_on': '2026-09-23',
}


def install_integration(app):
    @app.get('/api/manager')
    async def manager(language: str = Query('ru', pattern='^(ru|kk)$')):
        return {**CONTACTS, 'mode': 'direct_contact',
                'hours': 'Дс–Жм 09:00–18:00, Сб 09:00–13:00 (Алматы)' if language == 'kk' else 'Пн–Пт 09:00–18:00, Сб 09:00–13:00 (Алматы)',
                'message': 'Қоңырау шалыңыз немесе WhatsApp ашыңыз. Хабарлама тек оны өзіңіз жібергеннен кейін жеткізіледі.' if language == 'kk' else 'Позвоните или откройте WhatsApp. Сообщение будет отправлено только после вашего действия в WhatsApp.'}

    @app.get('/api/integration')
    async def integration():
        return {'catalog': 'EKT BasicAuth API, server-side', 'cart_provider': 'local_prototype',
                'cart_url': '/cart', 'official_cart_connected': False,
                'widget_script': '/widget.js', 'embed_url': '/?embed=1',
                'platform_evidence': 'Public EKT pages load /bitrix/ resources; no administrative access was available.',
                'required_for_production': ['EKT-approved basket API and session mapping', 'HTTPS hosting/reverse proxy', 'staging validation on EKT']}
