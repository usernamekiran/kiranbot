import pywikibot
import re
import os
import requests
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import time

site = pywikibot.Site('en', 'wikipedia')
max_edits = 50
edit_counter = 0

log_file = os.path.expanduser("~/enwiki/amp/logs/amp_log.txt")
change_file = os.path.expanduser("~/enwiki/amp/logs/amp_change.txt")
list_file = os.path.expanduser("~/enwiki/amp/logs/amp_list.txt")
skip_file = os.path.expanduser("~/enwiki/amp/logs/amp_skip.txt")
sink_file = os.path.expanduser("~/enwiki/amp/logs/amp_sink.txt")  # new file to track sinks

def check_for_run():
    # Re-fetch the control page each time to get the latest content
    amp_control_page = pywikibot.Page(site, "User:KiranBOT/shutoff/AMP")
    amp_control_page_text = teabot_control_page.text.lower()  # Fetch page content and convert to lowercase for case-insensitive search
    if "* run" not in amp_control_page_text:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write('* "* RUN" not present on `User:KiranBOT/shutoff/AMP`, exiting.')

# define AMP keywords to detect AMP links in URLs
AMP_KEYWORDS = [
    "/amp", "amp/", ".amp", "amp.", "?amp", "amp?", "=amp", 
    "amp=", "&amp", "amp&", "%amp", "amp%", "_amp", "amp_", 
    "-amp", "amp-", "/amp-", "-amp/", "amphtml", "_amphtml", 
    "-amphtml", "/amphtml", "amphtml/", "?amphtml", "amphtml=", "amphtml?"
]

def is_amp_url(url):
    parsed_url = urlparse(url)
    if 'amp.' in parsed_url.netloc:
        return True
    if any(keyword in parsed_url.path for keyword in AMP_KEYWORDS):
        return True
    query_params = dict(parse_qsl(parsed_url.query))
    for param, value in query_params.items():
        if 'amp' in param.lower() or value == 'amp':
            return True
    return False

def clean_amp_url(url):
    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    path = parsed_url.path
    query_params = dict(parse_qsl(parsed_url.query))

    if domain.startswith(('amp.', 'mobile-amp.')) or '.amp.' in domain:
        cleaned_domain = re.sub(r'\b(?:amp|mobile-amp)\.', '', domain).lstrip('.')
        parsed_url = parsed_url._replace(netloc=cleaned_domain)
        print(f"Subdomain cleaned: {cleaned_domain}")

    path_patterns = ['/amp/', '-amp/', '/amp-', '-amp', '/amphtml/', '-amphtml', 'amp_articleshow']
    for pattern in path_patterns:
        if pattern in path:
            path = path.replace(pattern, '/')
            print(f"Path cleaned from '{pattern}': {path}")

    if path.endswith('/amp'):
        path = path[:path.rfind('/amp')]
        print(f"Standalone '/amp' cleaned: {path}")

    suffix_patterns = [r'-amp(\.html|\.php|\.asp|\.htm|_section)?$', r'_amp(\.html|\.php)?$', r'amp_articleshow']
    for pattern in suffix_patterns:
        if re.search(pattern, path):
            path = re.sub(pattern, r'\1', path)
            print(f"Suffix pattern cleaned: {path}")

    parsed_url = parsed_url._replace(path=path)
    cleaned_query = {k: v for k, v in query_params.items() if 'amp' not in k.lower() and v.lower() not in ['amp', 'amphtml']}
    parsed_url = parsed_url._replace(query=urlencode(cleaned_query))

    cleaned_url = urlunparse(parsed_url)
    print(f"Cleaned URL: {cleaned_url}")

    return cleaned_url

def test_url(url):
    try:
        response = requests.head(url, allow_redirects=True, timeout=5)
        return response.url, response.status_code
    except requests.RequestException:
        return url, None

def clean_amp_url_with_test(url, title):
    ## cleans AMP artifacts from the URL, then verifies if the cleaned URL works
    ## if the cleaned URL fails but the original AMP URL works, skip and log the original URL

    # Clean the AMP URL
    cleaned_url = clean_amp_url(url)
    if cleaned_url == url:
        return url  # No change was made

    # test the original AMP URL and the cleaned URL
    original_final_url, original_status = test_url(url)
    cleaned_final_url, cleaned_status = test_url(cleaned_url)

    # case 1: original URL works, but cleaned URL fails
    if original_status == 200 and cleaned_status != 200:
        with open(skip_file, "a", encoding="utf-8") as f:
            f.write(f"* skipped (original works, cleaned fails): {title}\nOriginal URL: {url}\nCleaned URL: {cleaned_url}\n(Status: {cleaned_status})\n\n")
        return url  # return original AMP URL to skip this change

    # case 2: both original and cleaned URLs are errors (e.g., 404), proceed with cleaning
    if original_status != 200 and cleaned_status != 200:
        with open(list_file, "a", encoding="utf-8") as f:
            f.write(f"Article: {title}\nOld URL: {url}\nCleaned URL: {cleaned_url}\nResponse Status (both failed): Original={original_status}, Cleaned={cleaned_status}\n\n")
        return cleaned_url

    # case 3: cleaned URL works, proceed with replacement
    with open(list_file, "a", encoding="utf-8") as f:
        f.write(f"Article: {title}\nOld URL: {url}\nCleaned URL: {cleaned_url}\nResponse Status: {cleaned_status}\n\n")
    return cleaned_url

def find_and_replace_amp_links_in_refs(text, title):
    ref_pattern = re.compile(r'<ref[^>]*>(.*?)</ref>', re.DOTALL)
    matches = ref_pattern.findall(text)

    changes_made = False
    updated_text = text

    for ref in matches:
        print(f"Processing reference")
        urls_in_ref = re.findall(r'https?://[^\s|<]+', ref)
        for url in urls_in_ref:
            if is_amp_url(url):
                print(f"AMP URL detected: {url}")
                cleaned_url = clean_amp_url_with_test(url, title)
                if cleaned_url != url:
                    updated_ref = ref.replace(url, cleaned_url)
                    updated_text = updated_text.replace(ref, updated_ref)
                    print(f"Replaced AMP URL with Cleaned URL: {cleaned_url}")
                    changes_made = True

    return updated_text, changes_made

def process_templates(page, text):
    changes_made = False
    templates = page.templatesWithParams()

    for template, params in templates:
        template_name = template.title().lower()
        if template_name.startswith('cite'):
            for i, param in enumerate(params):
                if param.startswith('url=') or param.startswith('archive-url='):
                    key, value = param.split('=', 1)
                    url = value.strip()
                    if is_amp_url(url):
                        cleaned_url = clean_amp_url_with_test(url, page.title())
                        if cleaned_url != url:
                            params[i] = f"{key}={cleaned_url}"
                            changes_made = True
            updated_template = "{{" + template.title() + "|" + "|".join(params) + "}}"
            text = text.replace(str(template), updated_template)

    return text, changes_made

def find_and_replace_amp_links(text, page):
    updated_text, ref_changes_made = find_and_replace_amp_links_in_refs(text, page.title())
    updated_text, template_changes_made = process_templates(page, updated_text)
    changes_made = ref_changes_made or template_changes_made
    return updated_text, changes_made

def process_page(page, edit_counter):
#def process_page(page):
    #global edit_counter
    check_for_run()
    original_text = page.text
    updated_text, changes_made = find_and_replace_amp_links(original_text, page)

    if changes_made:
        print(f"Changes made to page: {page.title()}")
        page.text = updated_text
        page.save(summary="removed AMP tracking from URLs [[Wikipedia:Bots/Requests for approval/KiranBOT 12|BRFA 12.1]]", minor=True, botflag=True)
        edit_counter += 1
        time.sleep(120)
        with open(list_file, "a", encoding="utf-8") as f:
            f.write(f"{page.title()}\n")
        with open(change_file, "a", encoding="utf-8") as f:
            f.write(f"* updated text for {page.title()}:\n{updated_text}\n")
            f.write("="*40 + "\n")
        print(f"Updated page: {page.title()}")
    else:
        print(f"No changes made to page: {page.title()}")

    return edit_counter


def main():
    global edit_counter

    # path to the file containing article titles (modify this as needed)
    input_file = os.path.join(os.path.expanduser("~"), "enwiki", "amp", "input_file.txt")
    
    # open the file and read article titles line by line
    with open(input_file, 'r', encoding='utf-8') as f:
        # strip leading and trailing whitespace from each line (including the 4 spaces before each title)
        article_titles = [line.strip() for line in f.readlines() if line.strip()]

    # check if the input file contains titles
    if not article_titles:
        print(f"error: no article titles found in {input_file}.")
        return

    # iterate over each article title and process the corresponding page
    for title in article_titles:
        if edit_counter >= max_edits:  # check if max_edits has been reached
            print(f"Reached the maximum limit of {max_edits} edits. Exiting.")
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"Reached the maximum limit of {max_edits} edits. Exiting.\n")
            break  # stop further processing

        try:
            page = pywikibot.Page(site, title)  # fetch the page by title
            print(f"Processing page: {page.title()}")
            edit_counter = process_page(page, edit_counter)  # pass both page and edit_counter
        except Exception as e:
            print(f"Error processing page {title}: {e}")
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"* Failed to process {title}: {e}\n")

    # final summary of changes
    print(f"Total pages updated: {edit_counter}")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"* Total pages updated: {edit_counter}\n")

if __name__ == "__main__":
    main()
"""

def main():
    global edit_counter
    # go through each page in the main namespace (Namespace 0)
    for page in site.allpages(namespace=0):
        if edit_counter >= max_edits:
            print(f"Reached the maximum limit of {max_edits} edits. Exiting.")
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"Error processing {page.title()}: {e}\n")
            break
        print(f"Processing page: {page.title()}")
        try:
            edit_counter = process_page(page, edit_counter)
        except Exception as e:
            print(f"Error processing page {page.title()}: {e}")
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"* Error processing page {page.title()}: {e}")
    # Final summary of changes
    print(f"Total pages updated: {edit_counter}")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"* Total pages updated: {edit_counter}")
if __name__ == "__main__":
    main()
"""

"""
def main():
    global edit_counter
    # single page
    #page_title = "User:KiranBOT/sandbox/amp"
    page_title = "Charles III"
    page = pywikibot.Page(site, page_title)
    print(f"Processing page: {page.title()}")
    try:
        process_page(page)
    except Exception as e:
        print(f"Error processing page {page.title()}: {e}")
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"* Failed to save changes on {page.title()}: {e}\n")
    # Final summary of changes
    print(f"Total pages updated: {edit_counter}")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"* script exited.\n")

if __name__ == "__main__":
    main()
"""
