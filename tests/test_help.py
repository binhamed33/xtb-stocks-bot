import discord

from stockbot import help as h


def test_help_sections_fit_discord_limits():
    embeds = h.all_embeds()
    assert [k for k, _ in h.SECTIONS] == ["about", "commands", "sharia", "xtb", "portfolio", "faq"]
    for e in embeds:
        d = discord.Embed.from_dict(e)
        assert len(d) <= 6000 and len(d.fields) <= 25
        for f in d.fields:
            assert len(f.value) <= 1024 and len(f.name) <= 256
    txt = " ".join(str(e) for e in embeds)
    assert "CFD" in txt and "AAOIFI" in txt and "/analyze" in txt and "لا ينفذ" in txt
